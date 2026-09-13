"""Async structured reasoning with one bounded validation repair and no app tools."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.agents.mapping import MappingContext, new_id
from app.agents.provider_schemas import ProviderModel, assert_closed_schema
from app.config import Settings
from app.prompts import BOUNDARY_VERSION, boundary_prompt

# "route" added for the Event Router's semantic stage (app/events/router.py). Low effort:
# routing is a short relevance judgement over pre-filtered candidates, not graph reasoning,
# and it sits on the critical path of every ingested event.
Task = Literal["compile", "verify", "replan", "route"]
EFFORT = {"compile": "medium", "verify": "low", "replan": "medium", "route": "low"}


@dataclass(frozen=True)
class Prompt:
    version: str
    instructions: str


@dataclass(frozen=True)
class AttemptStats:
    model: str
    latency_ms: float
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    outcome: str = "parsed"


@dataclass(frozen=True)
class ProviderCall:
    task: Task
    model: str
    instructions: str
    input_json: str
    timeout_seconds: float
    max_output_tokens: int


@dataclass(frozen=True)
class ProviderReply[T: ProviderModel]:
    output: T
    stats: AttemptStats


class StructuredProvider(Protocol):
    async def generate[T: ProviderModel](
        self, call: ProviderCall, output_type: type[T]
    ) -> ProviderReply[T]: ...


class LLMError(RuntimeError):
    """Safe error for the caller. Provider bodies/keys are never included."""

    def __init__(self, code: str, message: str, attempts: tuple[AttemptStats, ...] = ()):
        self.code = code
        self.attempts = attempts
        super().__init__(message)


class InvalidOutput(ValueError):
    def __init__(self, errors: list[str], stats: AttemptStats | None = None):
        self.errors = errors
        self.stats = stats
        super().__init__("Invalid structured output")


def validation_feedback(error: ValueError) -> list[str]:
    if isinstance(error, ValidationError):
        return [
            f"{'.'.join(map(str, item['loc']))}: {item['type']}: {item['msg']}"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        ][:20]
    return [str(error)[:2000]]


class OpenAIProvider:
    """Own the SDK client; callers close it using async with or aclose().

    SDK transport retries are disabled. The boundary alone permits one retry for
    output validation; HTTP errors, refusals, and incomplete responses fail distinctly.
    """

    def __init__(self, settings: Settings, *, client: AsyncOpenAI | None = None):
        if client is None:
            if settings.api_key is None or not settings.api_key.get_secret_value().strip():
                raise LLMError(
                    "LLM_CREDENTIALS_MISSING", "OPENAI_API_KEY is required for live calls"
                )
            client = AsyncOpenAI(
                api_key=settings.api_key.get_secret_value(),
                timeout=settings.timeout_seconds,
                max_retries=0,
            )
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.close()

    async def generate[T: ProviderModel](
        self, call: ProviderCall, output_type: type[T]
    ) -> ProviderReply[T]:
        assert_closed_schema(output_type)
        started = perf_counter()
        stats = None
        try:
            # Inspect status before SDK parsing: incomplete JSON must not be
            # misclassified as a validation error by the SDK's output parser.
            raw = await self.client.with_options(max_retries=0).responses.with_raw_response.parse(
                model=call.model,
                instructions=call.instructions,
                input=[{"role": "user", "content": call.input_json}],
                text_format=output_type,
                reasoning={"effort": EFFORT[call.task]},
                timeout=call.timeout_seconds,
                max_output_tokens=call.max_output_tokens,
                store=False,
            )
            try:
                envelope = raw.http_response.json()
            except json.JSONDecodeError:
                raise LLMError(
                    "LLM_RESPONSE_FAILED", "Invalid provider response envelope"
                ) from None
            if not isinstance(envelope, dict):
                raise LLMError("LLM_RESPONSE_FAILED", "Invalid provider response envelope")
            usage = envelope.get("usage") or {}
            if not isinstance(usage, dict):
                raise LLMError("LLM_RESPONSE_FAILED", "Invalid provider usage envelope")
            stats = AttemptStats(
                model=envelope.get("model", call.model),
                response_id=envelope.get("id"),
                latency_ms=(perf_counter() - started) * 1000,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
            if envelope.get("status") == "incomplete":
                raise LLMError("LLM_INCOMPLETE", "Model output was incomplete", (stats,))
            if envelope.get("status") != "completed":
                raise LLMError("LLM_RESPONSE_FAILED", "Model response did not complete", (stats,))
            output_items = envelope.get("output")
            if not isinstance(output_items, list):
                raise LLMError("LLM_RESPONSE_FAILED", "Invalid provider output envelope", (stats,))
            for item in output_items:
                if not isinstance(item, dict):
                    raise LLMError("LLM_RESPONSE_FAILED", "Invalid provider output item", (stats,))
                if item.get("type") == "message":
                    content = item.get("content")
                    if not isinstance(content, list) or not all(
                        isinstance(part, dict) for part in content
                    ):
                        raise LLMError("LLM_RESPONSE_FAILED", "Invalid message envelope", (stats,))
                    if any(part.get("type") == "refusal" for part in content):
                        raise LLMError("LLM_REFUSED", "Model refused the request", (stats,))
            # The pinned SDK returns a legacy raw response with synchronous parse().
            parsed = raw.parse()
            outputs = [
                part.parsed
                for item in parsed.output
                if item.type == "message"
                for part in item.content
                if part.type == "output_text"
            ]
            if len(outputs) != 1 or outputs[0] is None:
                raise InvalidOutput(["Expected exactly one structured output"], stats)
            return ProviderReply(outputs[0], stats)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise InvalidOutput(validation_feedback(exc), stats) from None
        except APITimeoutError:
            raise LLMError("LLM_TIMEOUT", "Model request timed out") from None
        except (AuthenticationError, PermissionDeniedError):
            raise LLMError(
                "LLM_ACCESS_DENIED", "Model credentials or access were rejected"
            ) from None
        except RateLimitError:
            raise LLMError("LLM_RATE_LIMITED", "Model request was rate limited") from None
        except APIConnectionError:
            raise LLMError("LLM_UNAVAILABLE", "Could not reach model provider") from None
        except APIResponseValidationError:
            raise LLMError("LLM_RESPONSE_FAILED", "Invalid provider response envelope") from None
        except APIStatusError as exc:
            code = "LLM_REQUEST_REJECTED" if exc.status_code < 500 else "LLM_UNAVAILABLE"
            raise LLMError(code, "Model provider rejected or failed the request") from None


@dataclass(frozen=True)
class ReasoningResult[T]:
    value: T
    task: Task
    prompt_version: str
    boundary_version: str
    attempts: tuple[AttemptStats, ...]


@dataclass
class ReasoningBoundary:
    provider: StructuredProvider
    settings: Settings = field(default_factory=Settings)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    id_factory: Callable[[str], str] = new_id

    async def run[P: ProviderModel, D](
        self,
        *,
        task: Task,
        prompt: Prompt,
        request: BaseModel,
        output_type: type[P],
        convert: Callable[[P, MappingContext], D],
        validate: Callable[[D], object],
    ) -> ReasoningResult[D]:
        assert_closed_schema(output_type)
        if task not in EFFORT or not prompt.version.strip() or not prompt.instructions.strip():
            raise ValueError("A supported task and versioned trusted prompt are required")
        now = self.clock()
        context = MappingContext(now, self.settings.timezone, self.id_factory)
        envelope = {
            "request": request.model_dump(mode="json"),
            "reference_time": now.astimezone(ZoneInfo(self.settings.timezone)).isoformat(),
            "timezone": self.settings.timezone,
        }
        instructions = boundary_prompt() + "\n\n" + prompt.instructions
        attempts = []
        for _ in range(2):
            stats = None
            started = perf_counter()
            call = ProviderCall(
                task,
                self.settings.model,
                instructions,
                json.dumps(envelope, allow_nan=False),
                self.settings.timeout_seconds,
                self.settings.max_output_tokens,
            )
            try:
                async with asyncio.timeout(self.settings.timeout_seconds):
                    reply = await self.provider.generate(call, output_type)
                stats = reply.stats
                # Revalidate injected provider results as well as SDK-parsed results.
                output = output_type.model_validate(reply.output)
                value = convert(output, context)
                validate(value)
                attempts.append(replace(stats, outcome="validated"))
                return ReasoningResult(
                    value, task, prompt.version, BOUNDARY_VERSION, tuple(attempts)
                )
            except (InvalidOutput, ValueError) as exc:
                feedback = (
                    exc.errors if isinstance(exc, InvalidOutput) else validation_feedback(exc)
                )
                if isinstance(exc, InvalidOutput):
                    stats = exc.stats
                stats = stats or AttemptStats(call.model, (perf_counter() - started) * 1000)
                attempts.append(replace(stats, outcome="invalid"))
                envelope["validation_feedback"] = feedback
            except TimeoutError:
                attempts.append(
                    AttemptStats(
                        call.model, (perf_counter() - started) * 1000, outcome="LLM_TIMEOUT"
                    )
                )
                raise LLMError("LLM_TIMEOUT", "Model request timed out", tuple(attempts)) from None
            except LLMError as exc:
                failed = exc.attempts or (
                    AttemptStats(call.model, (perf_counter() - started) * 1000),
                )
                raise LLMError(
                    exc.code,
                    str(exc),
                    tuple(attempts) + tuple(replace(item, outcome=exc.code) for item in failed),
                ) from None
        raise LLMError(
            "LLM_OUTPUT_INVALID", "Model output failed validation twice", tuple(attempts)
        )
