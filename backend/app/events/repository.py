"""Read-only data access for the Event Router, plus the doubles its tests need.

The router never writes. This module exposes exactly two reads, both scoped to a single
`user_id`, because every routing query is multi-tenant and a missing tenant filter is the
failure mode that silently routes one person's event into another person's loop.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.events.router_models import (
    ROUTABLE_LOOP_STATUSES,
    LoopSummary,
    build_loop_summary,
)


@runtime_checkable
class LoopRepository(Protocol):
    """Everything the router is allowed to ask the database for."""

    async def list_routable_loops(self, user_id: str) -> list[LoopSummary]:
        """Every loop belonging to `user_id` whose status is in `ROUTABLE_LOOP_STATUSES`.

        Terminal loops are filtered in the query rather than by the caller, so there is no
        path by which a COMPLETED loop reaches the matcher.

            select l.*, n.*, r.*
            from loops l
            left join outcome_nodes n on n.loop_id = l.id
            left join evidence_requirements r on r.node_id = n.id
            where l.user_id = $1
              and l.status = any($2)      -- ROUTABLE_LOOP_STATUSES
            order by l.updated_at desc
        """
        ...

    async def find_loop_ids_by_thread(
        self, user_id: str, thread_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        """Map each thread id to the routable loops already linked to it.

        Returns a LIST of loop ids per thread, never a single id. One thread can
        legitimately belong to several loops — a single email chain covering two policy
        renewals, or a reply-all that two people's loops both track. Collapsing that to one
        id would silently drop a live candidate, and the drop would be invisible because
        the remaining match still looks correct. The fixtures carry exactly this
        collision on `thread_gmail_444`.

        Threads with no routable match are omitted from the result rather than mapped to an
        empty list, so a truthy lookup means "this thread is known".

            select coalesce(e.metadata->>'thread_id', e.metadata->>'thread_ts') as thread_id,
                   e.linked_loop_id
            from events e
            join loops l on l.id = e.linked_loop_id
            where l.user_id = $1
              and l.status = any($2)      -- ROUTABLE_LOOP_STATUSES
              and e.linked_loop_id is not null
              and coalesce(e.metadata->>'thread_id', e.metadata->>'thread_ts') = any($3)
            group by 1, 2

        The join runs through `events.linked_loop_id`, which `supabase/schema.sql`
        annotates as a routing hint. The tenant filter is on `loops.user_id` because
        `events` has no user column — the same gap that stops the router inferring a user
        from the event itself.
        """
        ...


class FixtureLoopRepository:
    """A `LoopRepository` backed by row dicts, for tests and offline demos.

    Takes rows rather than a path so that `app` never imports from `tests`. Callers in the
    test suite pass `tests.fixtures.router.load_loop_rows()`; `from_json_path` exists for
    scripts that want to point at a file directly.
    """

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = list(rows)

    @classmethod
    def from_json_path(cls, path: Path) -> "FixtureLoopRepository":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload["loops"])

    def _routable_rows(self, user_id: str) -> list[Mapping[str, Any]]:
        return [
            row
            for row in self._rows
            if row["loop"]["user_id"] == user_id
            and row["loop"]["status"] in ROUTABLE_LOOP_STATUSES
        ]

    async def list_routable_loops(self, user_id: str) -> list[LoopSummary]:
        return [
            build_loop_summary(
                loop_row=row["loop"],
                node_rows=row.get("nodes", []),
                requirement_rows=row.get("requirements", []),
                thread_ids=row.get("thread_ids", []),
            )
            for row in self._routable_rows(user_id)
        ]

    async def find_loop_ids_by_thread(
        self, user_id: str, thread_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        wanted = list(thread_ids)
        found: dict[str, list[str]] = {}
        for thread_id in wanted:
            matches = [
                row["loop"]["id"]
                for row in self._routable_rows(user_id)
                if thread_id in row.get("thread_ids", [])
            ]
            if matches:
                found[thread_id] = matches
        return found


class UnavailableLLM:
    """A model client that refuses to be called.

    Inject this wherever a test asserts a deterministic path completed without consulting
    a model. `complete` is a plain method rather than `async def` on purpose: it raises the
    moment it is called, so an unawaited `llm.complete(...)` still fails loudly instead of
    leaving a dangling coroutine and a passing test.
    """

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "The LLM was called on a path that must stay deterministic. "
            f"args={args!r} kwargs={kwargs!r}"
        )
