"""Builds the object graph once, at startup.

Everything downstream takes its collaborators as constructor arguments, which is what
lets the router and pipeline be tested against fixtures. That design only pays off if
exactly one place does the assembling — this is it.

Prerequisites are checked rather than assumed. The service is useful with ingestion
alone, so a missing database or a missing model key degrades to "events are still
polled, normalized and recorded" instead of refusing to start. `build_runtime`
returns `(None, reason)` in that case and the reason reaches /health.
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from datetime import UTC, datetime

from app.agents.compiler import OutcomeCompiler
from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.agents.replanner import Replanner
from app.agents.verifier import EvidenceVerifier
from app.config import Settings
from app.db import db
from app.events.pipeline import EventPipeline
from app.events.router import EventRouter
from app.runtime.adapters import PostgresLoopGraphSource, PostgresLoopRepository
from app.runtime.runtime import LoopRuntime

logger = logging.getLogger(__name__)

# Postgres connection settings are discrete PG* variables rather than one URL, so a
# password containing '@' or ':' needs no escaping. asyncpg reads them itself.
REQUIRED_PG_VARS = ("PGHOST", "PGUSER", "PGDATABASE")


def _now() -> datetime:
    return datetime.now(UTC)


def missing_prerequisites(settings: Settings) -> list[str]:
    """Everything the write path needs and does not have. Empty means ready."""
    missing = [name for name in REQUIRED_PG_VARS if not os.getenv(name)]
    problems = [f"{name} is not set" for name in missing]
    if settings.api_key is None:
        problems.append("OPENAI_API_KEY is not set")
    return problems


async def build_runtime(
    stack: AsyncExitStack,
    *,
    user_id: str,
    available_apps: list[str],
) -> tuple[LoopRuntime | None, list[str]]:
    """Assemble the write path, or explain why it cannot be assembled.

    `stack` owns the model provider's lifecycle: the agents take a boundary and never
    close the client underneath it, per the convention every agent handoff doc states.
    Registering it here ties the client's life to the application's.
    """
    settings = Settings.from_env()

    problems = missing_prerequisites(settings)
    if problems:
        return None, problems

    try:
        await db.init_pool()
    except Exception as exc:  # noqa: BLE001 - a bad DSN must not stop ingestion
        logger.exception("database unavailable")
        return None, [f"database unavailable: {exc}"]

    stack.push_async_callback(db.close_pool)

    provider = await stack.enter_async_context(OpenAIProvider(settings))
    boundary = ReasoningBoundary(provider, settings)

    router = EventRouter(
        repo=PostgresLoopRepository(),
        llm=provider,
        user_id=user_id,
        now_fn=_now,
        settings=settings,
    )
    pipeline = EventPipeline(
        router=router,
        graphs=PostgresLoopGraphSource(),
        verifier=EvidenceVerifier(boundary),
        user_id=user_id,
    )
    runtime = LoopRuntime(
        pipeline,
        user_id=user_id,
        available_apps=available_apps,
        compiler=OutcomeCompiler(boundary),
        replanner=Replanner(boundary),
    )
    return runtime, []
