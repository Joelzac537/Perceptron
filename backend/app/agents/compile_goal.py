"""Explicit live compiler entry point; writes no application or database state."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from app.agents.compiler import CompilerInputError, OutcomeCompiler
from app.agents.llm import LLMError, OpenAIProvider, ReasoningBoundary
from app.config import Settings
from app.graph.schemas import CompileGoalRequest


async def compile_request(
    request: CompileGoalRequest, settings: Settings, reference_time: datetime | None = None
) -> dict:
    async with OpenAIProvider(settings) as provider:
        boundary = ReasoningBoundary(provider, settings)
        if reference_time is not None:
            boundary.clock = lambda: reference_time
        result = await OutcomeCompiler(boundary).compile_with_metadata(request)
    return {
        "graph": result.value.model_dump(mode="json"),
        "diagnostics": {
            "validation": "passed",
            "prompt_version": result.prompt_version,
            "boundary_version": result.boundary_version,
            "attempts": [asdict(attempt) for attempt in result.attempts],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a goal into a validated outcome graph")
    parser.add_argument("--request", type=Path, required=True, help="CompileGoalRequest JSON file")
    parser.add_argument("--live", action="store_true", help="Allow billable model requests")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--reference-time", help="Optional timezone-aware ISO time for replay")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live explicitly to allow model requests")
    try:
        request = CompileGoalRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
        settings = Settings.from_env(env_file=args.env_file)
        reference = (
            TypeAdapter(AwareDatetime).validate_python(args.reference_time)
            if args.reference_time
            else None
        )
        report = asyncio.run(compile_request(request, settings, reference))
    except LLMError as exc:
        print(
            json.dumps(
                {"error_code": exc.code, "attempts": [asdict(attempt) for attempt in exc.attempts]},
                indent=2,
            )
        )
        return 1
    except (OSError, ValidationError, CompilerInputError):
        # Do not echo request bodies, configuration values, or credentials.
        print(json.dumps({"error_code": "VALIDATION_ERROR", "message": "Check input and settings"}))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
