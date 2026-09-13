"""
LoopGraph - database layer (asyncpg).

Owns all reads/writes to Postgres. Other modules call these functions; nobody
else writes SQL. Chosen over supabase-py because apply_graph_operations() needs
a real multi-statement transaction (atomic graph repair) that PostgREST cannot do.

Setup:
    pip install asyncpg
    # Connection settings live in .env as discrete PG* variables (not a single URL,
    # so a password containing '@' or ':' needs no escaping):
    #     PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE
    # Note the capital P in Perceptron_db.
    python db.py        # runs a self-contained smoke test against your DB

All jsonb columns accept/return plain dicts; text[] columns accept/return lists.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


def _gen(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Pass/receive Python dicts for jsonb and lists for arrays directly.
    for t in ("jsonb", "json"):
        await conn.set_type_codec(
            t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def init_pool(dsn: str | None = None) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # dsn=None -> asyncpg reads PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE from the
        # environment, so a password containing '@' or ':' can't break URL parsing.
        _pool = await asyncpg.create_pool(
            dsn,
            init=_init_conn,
            min_size=1,
            max_size=10,
        )
    return _pool


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Call init_pool() first.")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# --- events ----------------------------------------------------------------

async def insert_event(event: dict) -> tuple[dict, bool]:
    """
    Insert an external event. If the same (source_app, external_id) already
    exists, return the existing row instead of erroring. Returns (event, created).
    Turns duplicate-webhook dedup (test ID-01) into normal control flow: if
    created is False, the pipeline should skip re-processing.
    """
    eid = event.get("id") or _gen("event")
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into events
              (id, source_app, event_type, external_id, timestamp, actor, subject,
               content, attachments, metadata, linked_loop_id, processed)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,coalesce($12,false))
            on conflict (source_app, external_id) where external_id is not null
            do nothing
            returning *
            """,
            eid, event["source_app"], event["event_type"], event.get("external_id"),
            event.get("timestamp") or datetime.now(timezone.utc), event.get("actor"),
            event.get("subject"), event.get("content"),
            event.get("attachments", []), event.get("metadata", {}),
            event.get("linked_loop_id"), event.get("processed"),
        )
        if row is not None:
            return dict(row), True
        existing = await conn.fetchrow(
            "select * from events where source_app=$1 and external_id=$2",
            event["source_app"], event["external_id"],
        )
        return dict(existing), False


async def mark_event_processed(event_id: str) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "update events set processed=true where id=$1", event_id
        )


# --- graph persistence -----------------------------------------------------

async def persist_compiled_graph(
    loop: dict,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    evidence_requirements: list[dict] | None = None,
    actions: list[dict] | None = None,
    source_event_ids: list[str] | None = None,
) -> str:
    """
    Persist a freshly compiled graph atomically: loop + nodes + edges + evidence
    requirements + proposed actions, all-or-nothing. Returns the loop id.
    """
    nodes = nodes or []
    edges = edges or []
    evidence_requirements = evidence_requirements or []
    actions = actions or []
    # source events default: explicit arg, else the loop dict may carry them
    source_event_ids = source_event_ids or loop.get("source_event_ids", [])
    loop_id = loop.get("id") or _gen("loop")

    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                insert into loops (id, user_id, title, goal, status, root_node_id)
                values ($1,$2,$3,$4,coalesce($5,'ACTIVE'),$6)
                """,
                loop_id, loop["user_id"], loop["title"], loop["goal"],
                loop.get("status"), loop.get("root_node_id"),
            )
            # compilation metadata (assumptions / pending clarification) - kept out of
            # the strict Loop DTO, persisted so a reload doesn't lose a clarification.
            await conn.execute(
                """
                insert into compilations
                  (loop_id, assumptions, clarification_needed, clarification_question)
                values ($1,$2,$3,$4)
                """,
                loop_id, loop.get("assumptions", []),
                loop.get("clarification_needed", False),
                loop.get("clarification_question"),
            )
            # durable source-event provenance. Events must already exist (insert_event
            # first); on conflict do nothing because one event may seed several loops.
            for ev_id in source_event_ids:
                await conn.execute(
                    "insert into loop_source_events (loop_id, event_id) values ($1,$2) "
                    "on conflict do nothing",
                    loop_id, ev_id,
                )
            for n in nodes:
                await _insert_node(conn, loop_id, n)
            for e in edges:
                await _insert_edge(conn, loop_id, e)
            for r in evidence_requirements:
                await _insert_requirement(conn, r)
            for a in actions:
                await _insert_action(conn, loop_id, a)
    return loop_id


async def _insert_node(conn, loop_id, n):
    await conn.execute(
        """
        insert into outcome_nodes
          (id, loop_id, title, description, status, owner, deadline,
           recovery_strategy, metadata)
        values ($1,$2,$3,$4,coalesce($5,'PENDING'),$6,$7,$8,$9)
        """,
        n.get("id") or _gen("node"), loop_id, n["title"], n.get("description"),
        n.get("status"), n.get("owner"), n.get("deadline"),
        n.get("recovery_strategy"), n.get("metadata", {}),
    )


async def _insert_edge(conn, loop_id, e):
    await conn.execute(
        """
        insert into edges
          (id, loop_id, source_node_id, target_node_id, relationship, reason)
        values ($1,$2,$3,$4,$5,$6)
        on conflict on constraint uq_edge_semantic do nothing
        """,
        e.get("id") or _gen("edge"), loop_id, e["source_node_id"],
        e["target_node_id"], e["relationship"], e.get("reason"),
    )


async def _insert_requirement(conn, r):
    await conn.execute(
        """
        insert into evidence_requirements
          (id, node_id, type, description, source_apps, required_fields, must_all_match)
        values ($1,$2,$3,$4,$5,$6,coalesce($7,true))
        """,
        r.get("id") or _gen("evreq"), r["node_id"], r["type"], r["description"],
        r.get("source_apps", []), r.get("required_fields", {}), r.get("must_all_match"),
    )


async def _insert_action(conn, loop_id, a):
    await conn.execute(
        """
        insert into actions
          (id, loop_id, node_id, app, action_type, parameters, risk_level,
           requires_approval, status, idempotency_key, external_id, verification_method)
        values ($1,$2,$3,$4,$5,$6,$7,coalesce($8,false),coalesce($9,'PROPOSED'),$10,$11,$12)
        on conflict (idempotency_key) do nothing
        """,
        a.get("id") or _gen("action"), loop_id, a.get("node_id"), a["app"],
        a["action_type"], a.get("parameters", {}), a["risk_level"],
        a.get("requires_approval"), a.get("status"), a["idempotency_key"],
        a.get("external_id"), a.get("verification_method"),
    )


# --- actions ---------------------------------------------------------------

async def create_action(action: dict) -> tuple[dict, bool]:
    """
    Insert a proposed action. If an action with the same idempotency_key exists,
    return it instead of creating a duplicate (test ID-02). Returns (action, created).
    The executor should treat created=False as "already handled".
    """
    aid = action.get("id") or _gen("action")
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            insert into actions
              (id, loop_id, node_id, app, action_type, parameters, risk_level,
               requires_approval, status, idempotency_key, external_id, verification_method)
            values ($1,$2,$3,$4,$5,$6,$7,coalesce($8,false),coalesce($9,'PROPOSED'),$10,$11,$12)
            on conflict (idempotency_key) do nothing
            returning *
            """,
            aid, action["loop_id"], action.get("node_id"), action["app"],
            action["action_type"], action.get("parameters", {}), action["risk_level"],
            action.get("requires_approval"), action.get("status"),
            action["idempotency_key"], action.get("external_id"),
            action.get("verification_method"),
        )
        if row is not None:
            return dict(row), True
        existing = await conn.fetchrow(
            "select * from actions where idempotency_key=$1", action["idempotency_key"]
        )
        return dict(existing), False


async def update_action_status(
    action_id: str, status: str, external_id: str | None = None, error: str | None = None
) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            update actions
               set status=$2,
                   external_id=coalesce($3, external_id),
                   error=$4
             where id=$1
            """,
            action_id, status, external_id, error,
        )


# --- atomic graph repair (the self-healing keystone, doc 03 section 36) -----

async def apply_graph_operations(loop_id: str, operations: list[dict]) -> None:
    """
    Apply replanner graph operations atomically (all-or-nothing). Each op is:
      {"type": <GraphOperationType>, "target_id": <id|null>,
       "payload": {...}, "reason": <str>}
    Every op also writes an activity_log entry with its reason, so the UI can
    explain the repair. If any op fails, the whole repair rolls back - the graph
    is never left half-changed.
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            for op in operations:
                await _apply_one(conn, loop_id, op)


_NODE_COLS = {
    "title", "description", "status", "owner", "deadline",
    "recovery_strategy", "metadata",
}
_REQ_COLS = {"type", "description", "source_apps", "required_fields", "must_all_match"}


async def _apply_one(conn, loop_id, op):
    t = op["type"]
    tid = op.get("target_id")
    p = op.get("payload", {}) or {}
    reason = op.get("reason", "")

    if t == "ADD_NODE":
        await _insert_node(conn, p.get("loop_id", loop_id), p)
    elif t == "UPDATE_NODE":
        await _update_fields(conn, "outcome_nodes", _NODE_COLS, tid, p)
    elif t == "SUPERSEDE_NODE":
        await conn.execute("update outcome_nodes set status='SUPERSEDED' where id=$1", tid)
    elif t == "CANCEL_NODE":
        await conn.execute("update outcome_nodes set status='CANCELLED' where id=$1", tid)
    elif t == "VERIFY_NODE":
        await conn.execute("update outcome_nodes set status='VERIFIED' where id=$1", tid)
    elif t == "UPDATE_DEADLINE":
        await conn.execute(
            "update outcome_nodes set deadline=$2 where id=$1", tid, p.get("deadline")
        )
    elif t == "ADD_EDGE":
        await _insert_edge(conn, p.get("loop_id", loop_id), p)
    elif t == "REMOVE_EDGE":
        await conn.execute("delete from edges where id=$1", tid)
    elif t == "ADD_EVIDENCE_REQUIREMENT":
        await _insert_requirement(conn, p)
    elif t == "UPDATE_EVIDENCE_REQUIREMENT":
        await _update_fields(conn, "evidence_requirements", _REQ_COLS, tid, p)
    elif t == "ADD_ACTION":
        await _insert_action(conn, p.get("loop_id", loop_id), p)
    elif t == "CANCEL_ACTION":
        # Business rule (section 30): never cancel a VERIFIED action.
        await conn.execute(
            "update actions set status='CANCELLED' where id=$1 and status<>'VERIFIED'", tid
        )
    else:
        raise ValueError(f"Unknown graph operation type: {t}")

    await conn.execute(
        """
        insert into activity_logs (id, loop_id, activity_type, message, metadata)
        values ($1,$2,$3,$4,$5)
        """,
        _gen("activity"), loop_id, "GRAPH_REPAIRED", reason or t,
        {"operation": t, "target_id": tid},
    )


async def _update_fields(conn, table, allowed, row_id, payload):
    cols = [c for c in payload if c in allowed]
    if not cols:
        return
    sets = ", ".join(f"{c}=${i + 2}" for i, c in enumerate(cols))
    vals = [payload[c] for c in cols]
    await conn.execute(f"update {table} set {sets} where id=$1", row_id, *vals)


# --- reads -----------------------------------------------------------------

async def get_loop_detail(loop_id: str) -> dict | None:
    """Everything needed to render one loop (powers GET /api/loops/{id})."""
    async with pool().acquire() as conn:
        loop = await conn.fetchrow("select * from loops where id=$1", loop_id)
        if loop is None:
            return None
        nodes = await conn.fetch("select * from outcome_nodes where loop_id=$1", loop_id)
        edges = await conn.fetch("select * from edges where loop_id=$1", loop_id)
        node_ids = [n["id"] for n in nodes]
        evidence = (
            await conn.fetch(
                "select * from evidence where node_id = any($1::text[])", node_ids
            )
            if node_ids else []
        )
        actions = await conn.fetch("select * from actions where loop_id=$1", loop_id)
        approvals = await conn.fetch("select * from approvals where loop_id=$1", loop_id)
        activity = await conn.fetch(
            "select * from activity_logs where loop_id=$1 order by created_at", loop_id
        )
        source_events = await conn.fetch(
            "select event_id from loop_source_events where loop_id=$1", loop_id
        )
        requirements = (
            await conn.fetch(
                "select * from evidence_requirements where node_id = any($1::text[])",
                node_ids,
            )
            if node_ids else []
        )
        compilation = await conn.fetchrow(
            "select * from compilations where loop_id=$1", loop_id
        )
    return {
        "loop": dict(loop),
        "nodes": [dict(r) for r in nodes],
        "edges": [dict(r) for r in edges],
        "requirements": [dict(r) for r in requirements],
        "evidence": [dict(r) for r in evidence],
        "actions": [dict(r) for r in actions],
        "approvals": [dict(r) for r in approvals],
        "activity": [dict(r) for r in activity],
        "source_event_ids": [r["event_id"] for r in source_events],
        "compilation": dict(compilation) if compilation else None,
    }


async def list_loops() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("select * from loops order by created_at desc")
    return [dict(r) for r in rows]


async def get_pending_approvals() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("select * from approvals where status='PENDING'")
    return [dict(r) for r in rows]


# --- smoke test ------------------------------------------------------------

async def link_event_to_loop(event_id: str, loop_id: str) -> None:
    """Set events.linked_loop_id once routing has resolved it.

    The Event Router's cheapest signal reads this column back (it joins events on
    linked_loop_id to find loops already touching a thread), so without this write the
    column stays null forever and thread-based routing never matches anything.

    Only ever called for an unambiguous single match: the column holds one id, so a
    thread legitimately spanning two loops belongs in loop_source_events instead.
    """
    async with pool().acquire() as conn:
        await conn.execute(
            "update events set linked_loop_id=$2 where id=$1", event_id, loop_id
        )


async def save_evidence(event_id: str, decisions: list[dict]) -> list[str]:
    """Persist one assessed Evidence row per verifier decision.

    Every decision is stored, including UNRELATED and INSUFFICIENT: the assessment is
    the audit record, not just the favourable half of it. `verified` marks that the
    assessment happened and never that the node is complete -- completion gates are
    enforced separately by the runtime.
    """
    if not decisions:
        return []
    ids: list[str] = []
    async with pool().acquire() as conn, conn.transaction():
        for d in decisions:
            eid = d.get("id") or _gen("evidence")
            await conn.execute(
                """
                insert into evidence
                  (id, node_id, event_id, relationship, confidence, reason,
                   extracted_fields, verified)
                values ($1,$2,$3,$4,$5,$6,$7,$8)
                on conflict (id) do nothing
                """,
                eid, d["node_id"], event_id, str(d["relationship"]),
                float(d.get("confidence", 0.0)), d.get("reason", ""),
                d.get("extracted_fields") or {}, bool(d.get("verified", True)),
            )
            ids.append(eid)
    return ids


async def log_activity(
    loop_id: str, activity_type: str, message: str, metadata: dict | None = None
) -> str:
    """Append one activity entry. Powers the timeline the UI renders per loop."""
    aid = _gen("activity")
    async with pool().acquire() as conn:
        await conn.execute(
            """
            insert into activity_logs (id, loop_id, activity_type, message, metadata)
            values ($1,$2,$3,$4,$5)
            """,
            aid, loop_id, activity_type, message, metadata or {},
        )
    return aid


async def event_exists(dedup_key: str) -> bool:
    """True when an event carrying this dedup key has already been ingested.

    Matches on metadata->>'dedup_key' rather than the (source_app, external_id) unique
    index, because Event.dedup_key folds in the item's version stamp: an edited message
    is new work, while a re-delivered webhook is not. The unique index alone cannot
    express that distinction.
    """
    async with pool().acquire() as conn:
        found = await conn.fetchval(
            "select 1 from events where metadata->>'dedup_key' = $1 limit 1", dedup_key
        )
    return found is not None


async def loop_thread_ids(loop_id: str) -> list[str]:
    """Every conversation thread whose events are linked to this loop.

    Gmail stores it as metadata.thread_id and Slack as metadata.thread_ts; both are
    checked so neither app is privileged.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select distinct
                   coalesce(metadata->>'thread_id', metadata->>'thread_ts') as thread_id
            from events
            where linked_loop_id = $1
              and coalesce(metadata->>'thread_id', metadata->>'thread_ts') is not null
            """,
            loop_id,
        )
    return [r["thread_id"] for r in rows]


async def find_loop_ids_by_thread(
    user_id: str, thread_ids: list[str], statuses: list[str] | None = None
) -> dict[str, list[str]]:
    """Map each thread id to the still-open loops already linked to it.

    Returns a LIST per thread, never a single id: one conversation can legitimately
    belong to several loops, and collapsing that would silently drop a live candidate.

    Terminal loops are filtered here rather than by the caller, so there is no path by
    which a COMPLETED loop is reopened by a late message in its old thread. The tenant
    filter is on loops.user_id because events has no user column.
    """
    if not thread_ids:
        return {}
    statuses = statuses or ["ACTIVE", "WAITING", "BLOCKED"]
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            select coalesce(e.metadata->>'thread_id', e.metadata->>'thread_ts') as thread_id,
                   e.linked_loop_id
            from events e
            join loops l on l.id = e.linked_loop_id
            where l.user_id = $1
              and l.status = any($2::text[])
              and e.linked_loop_id is not null
              and coalesce(e.metadata->>'thread_id', e.metadata->>'thread_ts') = any($3::text[])
            group by 1, 2
            """,
            user_id, statuses, thread_ids,
        )
    found: dict[str, list[str]] = {}
    for row in rows:
        found.setdefault(row["thread_id"], []).append(row["linked_loop_id"])
    return found


async def _smoke():
    await init_pool()
    print("connected.")

    # a source event must exist before it can seed a loop (FK on loop_source_events)
    _, created = await insert_event({
        "id": "event_smoke", "source_app": "gmail", "event_type": "MESSAGE_RECEIVED",
        "external_id": "gmail_smoke_1", "content": "Return approved.",
    })
    print(f"insert_event: created={created}")

    loop_id = await persist_compiled_graph(
        loop={
            "id": "loop_smoke", "user_id": "user_1", "title": "Smoke",
            "goal": "Verify db.py works", "status": "ACTIVE",
            "root_node_id": "node_smoke_1",
            "assumptions": ["Merchant confirmation is sufficient refund evidence."],
            "clarification_needed": False,
        },
        nodes=[
            {"id": "node_smoke_1", "title": "Root outcome", "status": "WAITING",
             "metadata": {"order_id": "A1298", "amount": 129.0}},
            {"id": "node_smoke_2", "title": "Dependency", "status": "PENDING"},
        ],
        edges=[{"source_node_id": "node_smoke_1", "target_node_id": "node_smoke_2",
                "relationship": "DEPENDS_ON"}],
        source_event_ids=["event_smoke"],
    )
    print("persisted loop:", loop_id)

    _, c1 = await create_action({
        "loop_id": loop_id, "app": "google_calendar",
        "action_type": "CREATE_CALENDAR_EVENT", "risk_level": "LOW",
        "idempotency_key": "loop_smoke:deadline",
    })
    _, c2 = await create_action({
        "loop_id": loop_id, "app": "google_calendar",
        "action_type": "CREATE_CALENDAR_EVENT", "risk_level": "LOW",
        "idempotency_key": "loop_smoke:deadline",
    })
    print(f"create_action: first created={c1}, second created={c2}  (expect True then False)")

    await apply_graph_operations(loop_id, [
        {"type": "SUPERSEDE_NODE", "target_id": "node_smoke_1",
         "reason": "Superseded during smoke test."},
        {"type": "ADD_NODE",
         "payload": {"id": "node_smoke_3", "loop_id": loop_id,
                     "title": "Replacement", "status": "ACTIVE"},
         "reason": "Added replacement node."},
        {"type": "ADD_EDGE",
         "payload": {"loop_id": loop_id, "source_node_id": "node_smoke_3",
                     "target_node_id": "node_smoke_2", "relationship": "DEPENDS_ON"},
         "reason": "Relinked dependency to replacement."},
    ])

    detail = await get_loop_detail(loop_id)
    statuses = {n["id"]: n["status"] for n in detail["nodes"]}
    print(f"after repair: {len(detail['nodes'])} nodes, "
          f"{len(detail['edges'])} edges, {len(detail['activity'])} activity entries")
    print("node_smoke_1 status:", statuses.get("node_smoke_1"), "(expect SUPERSEDED)")
    print("source_event_ids:", detail["source_event_ids"], "(expect ['event_smoke'])")
    print("assumptions stored:", bool(detail["compilation"]
          and detail["compilation"]["assumptions"]), "(expect True)")

    async with pool().acquire() as conn:
        await conn.execute("delete from loops where id=$1", loop_id)
        await conn.execute("delete from events where id=$1", "event_smoke")
    print("cleaned up. db.py OK.")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(_smoke())