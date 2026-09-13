"""Live wiring: real model, real agents, in-memory state.

This is the handoff `events/sink.py` describes — the point where a normalized Event stops
being "ingested" and starts being reasoned about.

State lives in memory rather than Postgres, because asyncpg and a live database are not
required to demonstrate the pipeline. The important property is that state *accumulates*:
a loop compiled from one email becomes a routing candidate for the next, so a follow-up
message matches the goal its predecessor created. That closed loop is the whole product,
and it works here without a database.

Replacing this with the durable runtime means implementing `RuntimeStore`,
`LoopRepository` and `LoopGraphSource` over `app.db` — nothing above them changes.
"""

import asyncio
import logging
import os
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.agents.compiler import OutcomeCompiler
from app.agents.llm import LLMError, OpenAIProvider, ReasoningBoundary
from app.agents.replanner import Replanner
from app.agents.verifier import EvidenceVerifier
from app.config import Settings
from app.events.router import EventRouter
from app.events.router_models import ROUTABLE_LOOP_STATUSES, LoopSummary, build_loop_summary
from app.graph.schemas import CompiledGraph, Event
from app.graph.state import InMemoryRuntimeStore
from app.graph.workflow import EventWorkflow

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"

# Export .env into the process environment. Settings.from_env reads the file without
# exporting it, but app/db/db.py reads PG* straight from os.environ, so both layers
# need the same view. override=False keeps a real environment variable authoritative.
if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=False)

# The demo tenant. Composio's USER_ID identifies the connected accounts, not the
# LoopGraph user, so this stays a separate setting.
USER_ID = os.getenv("LOOPGRAPH_USER_ID", "user_001")

AVAILABLE_APPS = ["gmail", "slack", "google_drive", "google_calendar", "loopgraph"]

# Persist to Postgres when PGHOST is configured. Set LOOPGRAPH_PERSIST=0 to force the
# in-memory store even with a database available - useful when you want to exercise the
# pipeline without writing rows into a database your teammates share.
PERSIST = os.getenv("LOOPGRAPH_PERSIST", "1") != "0" and bool(os.getenv("PGHOST"))

# How many processed events to keep for the /pipeline endpoint.
HISTORY = 50


class LiveState:
    """Everything the pipeline has learned so far, held in memory.

    `graphs` is the source of truth: each compiled loop is stored whole, and both the
    router's candidate list and the verifier's hydration input are derived from it. One
    representation, so the two cannot drift apart.
    """

    def __init__(self) -> None:
        self.graphs: dict[str, dict[str, Any]] = {}
        self.results: deque[dict[str, Any]] = deque(maxlen=HISTORY)

    def add_compiled(self, graph: CompiledGraph) -> None:
        payload = graph.model_dump(mode="json")
        self.graphs[graph.loop.id] = {
            "loop": payload["loop"],
            "nodes": payload["nodes"],
            "edges": payload["edges"],
            "requirements": payload["evidence_requirements"],
            "actions": payload["proposed_actions"],
            "evidence": [],
            "state_revision": "rev-1",
            "thread_ids": [],
        }

    def remember_thread(self, loop_id: str, event: Event) -> None:
        """Record the event's thread against the loop it routed to.

        Gives the router's cheapest signal something to read on the next message in the
        same conversation, which is the in-memory stand-in for `events.linked_loop_id`.
        """
        rows = self.graphs.get(loop_id)
        if rows is None:
            return
        for key in ("thread_id", "thread_ts"):
            value = event.metadata.get(key)
            if isinstance(value, str) and value and value not in rows["thread_ids"]:
                rows["thread_ids"].append(value)

    def rows_for(self, user_id: str) -> list[dict[str, Any]]:
        return [rows for rows in self.graphs.values() if rows["loop"].get("user_id") == user_id]


class LiveRuntimeStore(InMemoryRuntimeStore):
    """Records writes, and feeds compiled loops back in as future candidates."""

    def __init__(self, state: LiveState) -> None:
        super().__init__()
        self._state = state

    async def save_compiled_graph(self, graph: CompiledGraph) -> None:
        await super().save_compiled_graph(graph)
        # This is what closes the loop: the next event can now match what this one made.
        self._state.add_compiled(graph)

    async def link_event_to_loop(self, event_id: str, loop_id: str) -> None:
        await super().link_event_to_loop(event_id, loop_id)
        event = self.events.get(event_id)
        if event is not None:
            self._state.remember_thread(loop_id, event)


class LiveLoopRepository:
    """A `LoopRepository` over `LiveState`."""

    def __init__(self, state: LiveState) -> None:
        self._state = state

    def _routable(self, user_id: str) -> list[dict[str, Any]]:
        return [
            rows
            for rows in self._state.rows_for(user_id)
            if rows["loop"].get("status") in ROUTABLE_LOOP_STATUSES
        ]

    async def list_routable_loops(self, user_id: str) -> list[LoopSummary]:
        return [
            build_loop_summary(
                loop_row=rows["loop"],
                node_rows=rows.get("nodes", []),
                requirement_rows=rows.get("requirements", []),
                thread_ids=rows.get("thread_ids", []),
            )
            for rows in self._routable(user_id)
        ]

    async def find_loop_ids_by_thread(
        self, user_id: str, thread_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for thread_id in thread_ids:
            matches = [
                rows["loop"]["id"]
                for rows in self._routable(user_id)
                if thread_id in rows.get("thread_ids", [])
            ]
            if matches:
                found[thread_id] = matches
        return found


class LiveLoopGraphSource:
    """A `LoopGraphSource` over `LiveState`."""

    def __init__(self, state: LiveState) -> None:
        self._state = state

    async def load_loop_graph(self, user_id: str, loop_id: str) -> dict[str, Any] | None:
        rows = self._state.graphs.get(loop_id)
        if rows is None or rows["loop"].get("user_id") != user_id:
            return None
        return dict(rows)


class LiveRuntime:
    """Owns the provider and the agents for the process lifetime."""

    def __init__(self) -> None:
        self.state = LiveState()
        self.store = LiveRuntimeStore(self.state)
        self.settings = Settings.from_env(env_file=ENV_FILE if ENV_FILE.exists() else None)
        self._provider: OpenAIProvider | None = None
        self._workflow: EventWorkflow | None = None
        self._pool_open = False
        self.persisting = False
        # The read side the API must use. Set in start() to whichever backend the
        # workflow itself got, so an endpoint can never report a different world from
        # the one the pipeline is writing into.
        self.repo: Any = LiveLoopRepository(self.state)
        self.graphs: Any = LiveLoopGraphSource(self.state)
        # One event at a time: the in-memory state has no transactions, and a demo does
        # not need concurrency badly enough to risk interleaved writes.
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self._workflow is not None

    async def start(self) -> None:
        if not self.settings.api_key:
            logger.warning("No OPENAI_API_KEY found; the reasoning pipeline will not run.")
            return

        # Durable or in-memory: the three protocols are the only seam, so nothing below
        # this point changes with the choice.
        repo = LiveLoopRepository(self.state)
        graphs = LiveLoopGraphSource(self.state)
        store = self.store
        if PERSIST:
            from app.db import db
            from app.graph.postgres_store import (
                PostgresLoopGraphSource,
                PostgresLoopRepository,
                PostgresRuntimeStore,
            )

            await db.init_pool()
            self._pool_open = True
            repo, graphs, store = (
                PostgresLoopRepository(),
                PostgresLoopGraphSource(),
                PostgresRuntimeStore(),
            )
        self.persisting = PERSIST
        self.repo, self.graphs = repo, graphs

        self._provider = OpenAIProvider(self.settings)
        boundary = ReasoningBoundary(self._provider, self.settings)
        self._workflow = EventWorkflow(
            router=EventRouter(
                repo=repo,
                llm=self._provider,
                user_id=USER_ID,
                now_fn=lambda: datetime.now(UTC),
            ),
            graphs=graphs,
            store=store,
            user_id=USER_ID,
            verifier=EvidenceVerifier(boundary),
            replanner=Replanner(boundary),
            compiler=OutcomeCompiler(boundary),
            available_apps=AVAILABLE_APPS,
        )

    async def stop(self) -> None:
        if self._provider is not None:
            await self._provider.aclose()
            self._provider = None
        if self._pool_open:
            from app.db import db

            await db.close_pool()
            self._pool_open = False
        self._workflow = None

    async def handle(self, event: Event) -> dict[str, Any]:
        """Run one event through the pipeline. Never raises into the caller.

        Ingestion has to keep working when reasoning fails, otherwise one bad model call
        stops the poller for every app.
        """
        if self._workflow is None:
            return {"event_id": event.id, "skipped": "pipeline not started"}

        started = datetime.now(UTC)
        writes_before = len(self.store.calls) if not self.persisting else 0
        async with self._lock:
            try:
                state = await self._workflow.run(event)
            except LLMError as exc:
                logger.warning("pipeline model error %s for %s", exc.code, event.id)
                result = {"event_id": event.id, "error_code": exc.code}
                self.state.results.append(result)
                return result
            except Exception as exc:  # noqa: BLE001 - ingestion must survive anything
                logger.exception("pipeline failed for %s", event.id)
                result = {"event_id": event.id, "error": str(exc)[:200]}
                self.state.results.append(result)
                return result

        # An event that CREATED a loop is that loop's source event, so its thread belongs
        # to the loop too. Without this, the next message in the same conversation falls
        # through to the model instead of resolving on the cheap deterministic path.
        created = state.get("compiled_loop_id")
        if created:
            self.state.remember_thread(created, event)

        result = {
            "event_id": event.id,
            "source_app": event.source_app,
            "event_type": event.event_type.value,
            "subject": event.subject,
            "at": started.isoformat(),
            "seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
            "duplicate": bool(state.get("duplicate")),
            "matches": [
                {"loop_id": m.loop_id, "confidence": m.confidence, "reason": m.reason}
                for m in (state.get("matches") or [])
            ],
            "created_loop": state.get("compiled_loop_id"),
            "verifications": {
                loop_id: [
                    {
                        "node_id": d.node_id,
                        "relationship": str(d.relationship),
                        "confidence": d.confidence,
                        "reason": d.reason,
                    }
                    for d in response.decisions
                ]
                for loop_id, response in (state.get("verifications") or {}).items()
            },
            "replanned": sorted((state.get("replans") or {}).keys()),
            "errors": list(state.get("errors") or []),
            "persisted": self.persisting,
            "writes": (
                ["postgres"] if self.persisting
                else [name for name, _ in self.store.calls[writes_before:]]
            ),
        }
        self.state.results.append(result)
        _log_result(result)
        return result


def _log_result(result: dict[str, Any]) -> None:
    if result.get("duplicate"):
        print("    -> duplicate, skipped")
        return
    if result["matches"]:
        for match in result["matches"]:
            reason = match["reason"][:70]
            print(f"    -> MATCHED {match['loop_id']} ({match['confidence']}) {reason}")
    elif result.get("created_loop"):
        print(f"    -> NEW LOOP {result['created_loop']}")
    else:
        print("    -> no match, no obligation")
    for loop_id, decisions in result["verifications"].items():
        for decision in decisions:
            print(f"       {loop_id}/{decision['node_id']}: {decision['relationship']}")
    for error in result["errors"]:
        print(f"       ! {error}")


runtime = LiveRuntime()
