"""Durable implementations of the three runtime protocols, over `app.db`.

This is the swap the rest of the pipeline was designed around: `RuntimeStore`,
`LoopRepository` and `LoopGraphSource` are the only seams, and nothing above them knows
whether state lives in memory or Postgres.

All SQL stays in `app/db/db.py`, per that module's rule that nobody else writes SQL.
This file translates between the domain DTOs and the row dicts that layer speaks.
"""

import logging
from collections.abc import Sequence
from typing import Any

from app.db import db
from app.events.router_models import ROUTABLE_LOOP_STATUSES, LoopSummary, build_loop_summary
from app.graph.schemas import CompiledGraph, Event, GraphOperation, NodeEvidenceDecision

logger = logging.getLogger(__name__)

# Written into events.metadata so `event_exists` can dedup on the version-aware key.
# The (source_app, external_id) unique index cannot express "an edit is new work".
DEDUP_METADATA_KEY = "dedup_key"


def _loop_rows(detail: dict[str, Any]) -> dict[str, Any]:
    """Reshape `get_loop_detail` output into the row shape hydration expects.

    `thread_ids` is derived from the loop's own source events rather than stored, so it
    cannot drift from what actually routed here.
    """
    return {
        "loop": detail["loop"],
        "nodes": detail.get("nodes", []),
        "edges": detail.get("edges", []),
        "requirements": detail.get("requirements", []),
        "actions": detail.get("actions", []),
        "evidence": detail.get("evidence", []),
        "state_revision": str(detail["loop"].get("updated_at") or "unknown"),
        "thread_ids": detail.get("thread_ids", []),
    }


class PostgresRuntimeStore:
    """Every write the pipeline performs, against the real database."""

    async def is_duplicate(self, dedup_key: str) -> bool:
        return await db.event_exists(dedup_key)

    async def save_event(self, event: Event) -> None:
        # mode="python" throughout: asyncpg binds timestamptz from real datetime objects
        # and rejects ISO strings, which is exactly what mode="json" would produce.
        payload = event.model_dump(mode="python")
        # Carry the version-aware key so `event_exists` can find it next time.
        payload["metadata"] = {**payload.get("metadata", {}), DEDUP_METADATA_KEY: event.dedup_key}
        _row, created = await db.insert_event(payload)
        if not created:
            # The (source_app, external_id) index rejected it even though the dedup key
            # was new: this is an edited message. The pipeline still processes the new
            # content from the in-memory Event; only the extra row is skipped. Giving an
            # edit its own row needs a version column in the events table.
            logger.info("event %s matched an existing external_id; no new row", event.id)

    async def link_event_to_loop(self, event_id: str, loop_id: str) -> None:
        await db.link_event_to_loop(event_id, loop_id)

    async def save_evidence(
        self, loop_id: str, event_id: str, decisions: Sequence[NodeEvidenceDecision]
    ) -> None:
        await db.save_evidence(
            event_id,
            [
                {
                    "node_id": d.node_id,
                    "relationship": str(d.relationship),
                    "confidence": d.confidence,
                    "reason": d.reason,
                    "extracted_fields": d.extracted_fields,
                    "verified": True,
                }
                for d in decisions
            ],
        )

    async def apply_operations(
        self, loop_id: str, operations: Sequence[GraphOperation]
    ) -> None:
        await db.apply_graph_operations(
            loop_id, [op.model_dump(mode="json") for op in operations]
        )

    async def save_compiled_graph(self, graph: CompiledGraph) -> None:
        # mode="python" keeps deadlines and created_at as datetimes for asyncpg. JSON
        # strings bind fine to text columns but raise on timestamptz.
        payload = graph.model_dump(mode="python")
        await db.persist_compiled_graph(
            loop=payload["loop"],
            nodes=payload["nodes"],
            edges=payload["edges"],
            evidence_requirements=payload["evidence_requirements"],
            actions=payload["proposed_actions"],
            source_event_ids=payload["loop"].get("source_event_ids") or [],
        )

    async def mark_processed(self, event_id: str) -> None:
        await db.mark_event_processed(event_id)

    async def log_activity(self, loop_id: str, activity_type: str, message: str) -> None:
        await db.log_activity(loop_id, activity_type, message)


class PostgresLoopRepository:
    """The router's read side, scoped to one tenant."""

    async def list_routable_loops(self, user_id: str) -> list[LoopSummary]:
        loops = await db.list_loops()
        summaries: list[LoopSummary] = []
        for row in loops:
            if row.get("user_id") != user_id or row.get("status") not in ROUTABLE_LOOP_STATUSES:
                continue
            detail = await db.get_loop_detail(row["id"])
            if detail is None:
                continue
            summaries.append(
                build_loop_summary(
                    loop_row=detail["loop"],
                    node_rows=detail.get("nodes", []),
                    requirement_rows=detail.get("requirements", []),
                    thread_ids=await self._thread_ids(row["id"]),
                )
            )
        return summaries

    @staticmethod
    async def _thread_ids(loop_id: str) -> list[str]:
        return await db.loop_thread_ids(loop_id)

    async def find_loop_ids_by_thread(
        self, user_id: str, thread_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        found = await db.find_loop_ids_by_thread(user_id, list(thread_ids))
        return {thread: ids for thread, ids in found.items() if ids}


class PostgresLoopGraphSource:
    """The verifier's read side: one loop's full graph, tenant-checked."""

    async def load_loop_graph(self, user_id: str, loop_id: str) -> dict[str, Any] | None:
        detail = await db.get_loop_detail(loop_id)
        if detail is None or detail["loop"].get("user_id") != user_id:
            return None
        detail["thread_ids"] = await db.loop_thread_ids(loop_id)
        return _loop_rows(detail)
