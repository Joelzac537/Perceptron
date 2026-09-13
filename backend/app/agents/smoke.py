"""Opt-in live check: python -m app.agents.smoke --live [--env-file .env]."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel

from app.agents.llm import LLMError, OpenAIProvider, Prompt, ReasoningBoundary
from app.agents.mapping import map_verify
from app.agents.provider_schemas import VerifyDraft
from app.config import Settings
from app.graph.schemas import VerifyEventResponse


class SmokeRequest(BaseModel):
    node_id: str = "node_smoke"
    required_evidence: str = "A final document must have been received."
    observed_event: str = "No document or other supporting evidence is available."


def validate_smoke(response: VerifyEventResponse) -> None:
    if len(response.decisions) != 1:
        raise ValueError("Expected one decision")
    decision = response.decisions[0]
    if decision.relationship != "INSUFFICIENT" or decision.evidence_satisfies_requirement:
        raise ValueError("Missing evidence must be INSUFFICIENT and cannot satisfy the requirement")
    if response.requires_replan:
        raise ValueError("No changed information requires a replan")


async def smoke(settings: Settings) -> dict:
    async with OpenAIProvider(settings) as provider:
        result = await ReasoningBoundary(provider, settings).run(
            task="verify",
            prompt=Prompt(
                "smoke-v1", "Assess the supplied synthetic evidence. Return one decision."
            ),
            request=SmokeRequest(),
            output_type=VerifyDraft,
            convert=lambda output, _: map_verify(output, {"node_smoke"}),
            validate=validate_smoke,
        )
    return {
        "validation": "passed",
        "task": result.task,
        "prompt_version": result.prompt_version,
        "boundary_version": result.boundary_version,
        "attempts": [asdict(attempt) for attempt in result.attempts],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in live structured-output smoke check")
    parser.add_argument("--live", action="store_true", help="Allow a billable model request")
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live explicitly to allow the model request")
    settings = Settings.from_env(env_file=args.env_file)
    try:
        report = asyncio.run(smoke(settings))
    except LLMError as exc:
        print(
            json.dumps(
                {
                    "validation": "failed",
                    "error_code": exc.code,
                    "attempts": [asdict(attempt) for attempt in exc.attempts],
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
