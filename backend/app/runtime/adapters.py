"""Postgres-backed implementations of the read protocols the router and pipeline take.

They are deliberately thin. All SQL lives in `app.db.queries`; these exist so the
read side can depend on a protocol it defines rather than on the database, which is
what lets the whole router test suite run against fixtures with no Postgres at all.

`FixtureLoopRepository` and `FixtureLoopGraphSource` remain the offline equivalents —
same protocols, same call signatures, so swapping one for the other is a wiring change
and nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.db import queries
from app.events.router_models import LoopSummary, build_loop_summary


class PostgresLoopRepository:
    """`LoopRepository` over the real database.

    `user_id` is passed per call rather than held on the instance, matching the
    protocol: one repository serves every tenant, and the filter travels with the
    question so it cannot be forgotten at construction time.
    """

    async def list_routable_loops(self, user_id: str) -> list[LoopSummary]:
        rows = await queries.list_routable_loop_rows(user_id)
        return [
            build_loop_summary(
                loop_row=row["loop"],
                node_rows=row["nodes"],
                requirement_rows=row["requirements"],
                thread_ids=row["thread_ids"],
            )
            for row in rows
        ]

    async def find_loop_ids_by_thread(
        self, user_id: str, thread_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        return await queries.find_loop_ids_by_thread(user_id, list(thread_ids))


class PostgresLoopGraphSource:
    """`LoopGraphSource` over the real database."""

    async def load_loop_graph(self, user_id: str, loop_id: str) -> dict[str, Any] | None:
        return await queries.load_loop_graph(user_id, loop_id)
