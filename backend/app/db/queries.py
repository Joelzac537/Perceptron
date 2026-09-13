"""Reads and writes the runtime needs that `db.py` does not already provide.

A separate module rather than additions to `db.py` so the two can be merged
independently — `db.py` is owned elsewhere. The convention it states still holds
here: SQL lives in the `app.db` package and nowhere else. `app/runtime/adapters.py`
wraps these in the protocols the router and verifier expect, and nothing outside
this package writes a query.

Every read is scoped by `user_id`. `Event` carries no user, so these functions and
the graph source are the only places tenancy can be enforced; a missing filter here
routes one person's event into another person's loop, which is the failure mode the
router's own docstring calls out.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.db.db import pool

# Loops a new event can still be routed to. Terminal loops are excluded in the
# query rather than by the caller, so no COMPLETED loop can reach the matcher.
# Mirrors ROUTABLE_LOOP_STATUSES in app/events/router_models.py, restated as plain
# strings because that module must not be imported by the write layer.
ROUTABLE_STATUSES: tuple[str, ...] = ("ACTIVE", "WAITING", "BLOCKED")

# Both keys one poller or another writes a conversation id under: Gmail threads use
# `thread_id`, Slack uses `thread_ts`. Coalesced so a thread lookup does not have to
# know which app produced the event.
_THREAD_KEY = "coalesce(e.metadata->>'thread_id', e.metadata->>'thread_ts')"


def _gen(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --- routing reads ---------------------------------------------------------


async def list_routable_loop_rows(user_id: str) -> list[dict[str, Any]]:
    """Every routable loop for one user, shaped for `build_loop_summary`.

    Four queries assembled in Python rather than one join: the join would repeat each
    loop row once per node per requirement, and the summary builder wants them grouped
    anyway. At hackathon volume the round trips are free.
    """
    async with pool().acquire() as conn:
        loops = await conn.fetch(
            "select * from loops where user_id=$1 and status = any($2::text[]) "
            "order by updated_at desc",
            user_id,
            list(ROUTABLE_STATUSES),
        )
        if not loops:
            return []

        loop_ids = [row["id"] for row in loops]
        nodes = await conn.fetch(
            "select * from outcome_nodes where loop_id = any($1::text[])", loop_ids
        )
        node_ids = [row["id"] for row in nodes]
        requirements = (
            await conn.fetch(
                "select * from evidence_requirements where node_id = any($1::text[])",
                node_ids,
            )
            if node_ids
            else []
        )
        threads = await conn.fetch(
            f"select linked_loop_id, {_THREAD_KEY} as thread_id from events e "
            f"where linked_loop_id = any($1::text[]) and {_THREAD_KEY} is not null",
            loop_ids,
        )

    nodes_by_loop: dict[str, list[dict]] = {}
    for row in nodes:
        nodes_by_loop.setdefault(row["loop_id"], []).append(dict(row))

    requirements_by_loop: dict[str, list[dict]] = {}
    node_to_loop = {row["id"]: row["loop_id"] for row in nodes}
    for row in requirements:
        loop_id = node_to_loop.get(row["node_id"])
        if loop_id:
            requirements_by_loop.setdefault(loop_id, []).append(dict(row))

    threads_by_loop: dict[str, list[str]] = {}
    for row in threads:
        bucket = threads_by_loop.setdefault(row["linked_loop_id"], [])
        if row["thread_id"] not in bucket:
            bucket.append(row["thread_id"])

    return [
        {
            "loop": dict(loop),
            "nodes": nodes_by_loop.get(loop["id"], []),
            "requirements": requirements_by_loop.get(loop["id"], []),
            "thread_ids": threads_by_loop.get(loop["id"], []),
        }
        for loop in loops
    ]


async def find_loop_ids_by_thread(
    user_id: str, thread_ids: list[str]
) -> dict[str, list[str]]:
    """Map each thread id to the routable loops already linked to it.

    A list per thread, never a single id: one thread can legitimately belong to
    several loops, and collapsing that would silently drop a live candidate.
    Threads with no match are omitted, so a truthy lookup means "this thread is known".
    """
    if not thread_ids:
        return {}

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            select {_THREAD_KEY} as thread_id, e.linked_loop_id
              from events e
              join loops l on l.id = e.linked_loop_id
             where l.user_id = $1
               and l.status = any($2::text[])
               and {_THREAD_KEY} = any($3::text[])
            """,
            user_id,
            list(ROUTABLE_STATUSES),
            list(thread_ids),
        )

    found: dict[str, list[str]] = {}
    for row in rows:
        bucket = found.setdefault(row["thread_id"], [])
        if row["linked_loop_id"] not in bucket:
            bucket.append(row["linked_loop_id"])
    return found


# --- verification reads ----------------------------------------------------


async def load_loop_graph(user_id: str, loop_id: str) -> dict[str, Any] | None:
    """One loop's full graph, or None when it does not exist for this user.

    Returns raw rows in the shape `build_verify_request` expects. It drops the
    derived columns itself, so nothing is trimmed here — a row that disagrees with
    the DTO should surface as a validation error rather than be silently reshaped.
    """
    async with pool().acquire() as conn:
        loop = await conn.fetchrow(
            "select * from loops where id=$1 and user_id=$2", loop_id, user_id
        )
        if loop is None:
            return None

        nodes = await conn.fetch("select * from outcome_nodes where loop_id=$1", loop_id)
        edges = await conn.fetch("select * from edges where loop_id=$1", loop_id)
        node_ids = [row["id"] for row in nodes]
        requirements = (
            await conn.fetch(
                "select * from evidence_requirements where node_id = any($1::text[])",
                node_ids,
            )
            if node_ids
            else []
        )
        source_events = await conn.fetch(
            "select event_id from loop_source_events where loop_id=$1", loop_id
        )

    return {
        "loop": {
            **dict(loop),
            "source_event_ids": [row["event_id"] for row in source_events],
        },
        "nodes": [dict(row) for row in nodes],
        "edges": [dict(row) for row in edges],
        "requirements": [dict(row) for row in requirements],
    }


async def load_replan_context_rows(loop_id: str) -> dict[str, list[dict]]:
    """Requirements, actions and existing evidence for one loop.

    The replanner's `ReplanContext` needs all three, and none of them come back from
    `load_loop_graph` in the shape it wants.
    """
    async with pool().acquire() as conn:
        nodes = await conn.fetch("select id from outcome_nodes where loop_id=$1", loop_id)
        node_ids = [row["id"] for row in nodes]
        requirements = (
            await conn.fetch(
                "select * from evidence_requirements where node_id = any($1::text[])",
                node_ids,
            )
            if node_ids
            else []
        )
        evidence = (
            await conn.fetch(
                "select * from evidence where node_id = any($1::text[])", node_ids
            )
            if node_ids
            else []
        )
        actions = await conn.fetch("select * from actions where loop_id=$1", loop_id)

    return {
        "requirements": [dict(row) for row in requirements],
        "evidence": [dict(row) for row in evidence],
        "actions": [dict(row) for row in actions],
    }


async def applied_event_ids(loop_id: str) -> list[str]:
    """Events already used to repair this loop.

    The replanner refuses to act twice on the same event; this is what makes that
    check meaningful across restarts rather than only within one process.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "select distinct event_id from evidence e "
            "join outcome_nodes n on n.id = e.node_id where n.loop_id=$1",
            loop_id,
        )
    return [row["event_id"] for row in rows]


# --- writes ----------------------------------------------------------------


async def insert_evidence(
    *,
    node_id: str,
    event_id: str,
    relationship: str,
    confidence: float,
    reason: str,
    extracted_fields: dict | None = None,
    verified: bool = False,
) -> str:
    """Record one verifier decision against one node. Returns the evidence id."""
    evidence_id = _gen("evidence")
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into evidence
              (id, node_id, event_id, relationship, confidence, reason,
               extracted_fields, verified)
            values ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            evidence_id,
            node_id,
            event_id,
            relationship,
            confidence,
            reason,
            extracted_fields or {},
            verified,
        )
    return evidence_id


async def link_event_to_loop(event_id: str, loop_id: str) -> None:
    """Set the routing hint on an event.

    Only ever called when exactly one loop matched. A shared thread matches several
    loops and this column holds one id, so the ambiguous case is left null and the
    provenance lives in `loop_source_events` instead.
    """
    async with pool().acquire() as conn:
        await conn.execute(
            "update events set linked_loop_id=$2 where id=$1", event_id, loop_id
        )


async def record_source_event(loop_id: str, event_id: str) -> None:
    """Durable provenance for an event that contributed to a loop."""
    async with pool().acquire() as conn:
        await conn.execute(
            "insert into loop_source_events (loop_id, event_id) values ($1,$2) "
            "on conflict do nothing",
            loop_id,
            event_id,
        )


# --- reads for the UI ------------------------------------------------------


async def runtime_stats(user_id: str) -> dict[str, int]:
    """Counts describing what each agent has actually done for this user.

    One round trip. Every count is scoped to the user's own loops, so a shared
    database does not inflate one person's numbers with another's work — the
    exception is `events`, which carries no user column and is therefore counted
    globally. That is a known gap in the shared contract, not an oversight here.
    """
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            select
              (select count(*) from events)                                as events_total,
              (select count(*) from events where processed)                as events_processed,
              (select count(*) from events where linked_loop_id is not null) as events_linked,
              (select count(*) from loops where user_id = $1)              as loops_total,
              (select count(*) from loops
                 where user_id = $1 and status = 'COMPLETED')              as loops_completed,
              (select count(*) from outcome_nodes n
                 join loops l on l.id = n.loop_id where l.user_id = $1)    as nodes_total,
              (select count(*) from outcome_nodes n
                 join loops l on l.id = n.loop_id
                where l.user_id = $1 and n.status = 'VERIFIED')            as nodes_verified,
              (select count(*) from evidence e
                 join outcome_nodes n on n.id = e.node_id
                 join loops l on l.id = n.loop_id where l.user_id = $1)    as evidence_total,
              (select count(*) from evidence e
                 join outcome_nodes n on n.id = e.node_id
                 join loops l on l.id = n.loop_id
                where l.user_id = $1 and e.verified)                       as evidence_proving,
              (select count(*) from activity_logs a
                 join loops l on l.id = a.loop_id
                where l.user_id = $1 and a.activity_type = 'GRAPH_REPAIRED') as repairs,
              (select count(*) from actions a
                 join loops l on l.id = a.loop_id where l.user_id = $1)    as actions_total,
              (select count(*) from compilations c
                 join loops l on l.id = c.loop_id
                where l.user_id = $1 and c.clarification_needed)           as clarifications
            """,
            user_id,
        )
    return {key: int(value) for key, value in dict(row).items()}


async def list_loops_for_user(user_id: str) -> list[dict[str, Any]]:
    """Loop list with node counts, for the dashboard."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select l.*,
                   count(n.id) as node_count,
                   count(n.id) filter (where n.status = 'VERIFIED') as verified_count
              from loops l
              left join outcome_nodes n on n.loop_id = l.id
             where l.user_id = $1
             group by l.id
             order by l.updated_at desc
            """,
            user_id,
        )
    return [dict(row) for row in rows]
