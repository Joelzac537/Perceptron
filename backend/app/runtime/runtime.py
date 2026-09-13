"""Applies an `EventPlan`. The half of the pipeline that is allowed to write.

`EventPipeline.process` decides what should happen to an event and performs none of
it; this turns that plan into rows. Keeping the split means the deciding half stays
testable without a database, and the writing half has no opinions to test.

One pass over one event:

    insert the event          -- the unique index is the real dedup, not our set
    plan it                   -- routing, then verification per matched loop
    apply                     -- compile a new loop, or record evidence and repair
    mark it processed

Failures are contained per loop. One unloadable or unverifiable loop degrades to an
unlinked match and the other matches still get assessed, because a single bad graph
must not stop an event being applied to the loops that are fine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.db import db, queries
from app.events.hydration import HydrationError, build_verify_request
from app.events.pipeline import EventPipeline, LoopOutcome
from app.graph.repair_schemas import ReplanContext
from app.graph.schemas import (
    Action,
    CompileGoalRequest,
    Edge,
    Event,
    Evidence,
    EvidenceRequirement,
    ReplanRequest,
)

logger = logging.getLogger(__name__)

# A node is only marked VERIFIED through a graph operation, never a bare UPDATE, so
# every status change carries a reason into activity_logs and the UI can explain it.
VERIFY_NODE = "VERIFY_NODE"


def _plain(value: Any) -> Any:
    """Enum -> its value, recursively. Leaves datetimes and everything else alone.

    `EventType` and friends subclass `str`, so asyncpg would encode them correctly
    anyway, but a plain dict is what `db.py` documents as its input and relying on
    the subclassing is the kind of thing that breaks quietly when a type changes.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


@dataclass
class RuntimeResult:
    """What one pass actually did. Returned for logging and the UI, not control flow."""

    event_id: str
    stored: bool = False
    duplicate: bool = False
    matched_loop_ids: tuple[str, ...] = ()
    compiled_loop_id: str | None = None
    evidence_written: int = 0
    nodes_verified: int = 0
    repaired_loop_ids: tuple[str, ...] = ()
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.duplicate:
            return f"{self.event_id}: duplicate, already stored"
        parts: list[str] = []
        if self.compiled_loop_id:
            parts.append(f"compiled {self.compiled_loop_id}")
        if self.matched_loop_ids:
            parts.append(f"matched {', '.join(self.matched_loop_ids)}")
        if self.evidence_written:
            parts.append(f"{self.evidence_written} evidence")
        if self.nodes_verified:
            parts.append(f"{self.nodes_verified} verified")
        if self.repaired_loop_ids:
            parts.append(f"repaired {', '.join(self.repaired_loop_ids)}")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return f"{self.event_id}: " + ("; ".join(parts) or "no action")


class LoopRuntime:
    """Owns the write side of one event.

    `compiler` and `replanner` are optional so the service can run with ingestion and
    routing live but no model configured — the event is still stored and still routed,
    which is most of the value and costs nothing when OPENAI_API_KEY is absent.
    """

    def __init__(
        self,
        pipeline: EventPipeline,
        *,
        user_id: str,
        available_apps: list[str],
        compiler: Any | None = None,
        replanner: Any | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._user_id = user_id
        self._available_apps = list(available_apps)
        self._compiler = compiler
        self._replanner = replanner

    async def handle(self, event: Event) -> RuntimeResult:
        result = RuntimeResult(event_id=event.id)

        row, created = await db.insert_event(_plain(event.model_dump()))
        result.stored = created
        if not created:
            # The unique index on (source_app, external_id) already had it. The sink's
            # in-memory set catches this first in the normal case; this catches a
            # restart, where the set is empty but the row is not.
            result.duplicate = True
            return result

        # Use the id the database actually holds. insert_event generates one when the
        # event arrives without it, and every later write is a foreign key onto that.
        stored_event = event.model_copy(update={"id": row["id"]})

        try:
            plan = await self._pipeline.process(stored_event)
        except Exception as exc:  # noqa: BLE001 - a throttled model must not kill the poller
            # The row stays processed=false on purpose: it genuinely has not been
            # processed, so a later pass can pick it up. What must not happen is the
            # failure escaping into the sink's generic handler guard, where it loses
            # both the event id and the stack.
            result.errors.append(f"routing failed: {exc}")
            logger.exception("routing failed for event %s", stored_event.id)
            return result

        result.matched_loop_ids = plan.matched_loop_ids

        for reason in plan.skipped:
            result.errors.append(reason)
            logger.warning("event %s: %s", stored_event.id, reason)

        if plan.compile_new_loop:
            await self._compile(stored_event, result)
        else:
            for outcome in plan.outcomes:
                await self._apply_outcome(stored_event, outcome, result)

            if plan.link_loop_id:
                await queries.link_event_to_loop(stored_event.id, plan.link_loop_id)

        await db.mark_event_processed(stored_event.id)
        return result

    # --- new obligations ---------------------------------------------------

    async def _compile(self, event: Event, result: RuntimeResult) -> None:
        """Turn an unmatched event into a new loop.

        Only reached when routing found no match. An event that belongs to an existing
        goal is evidence about that goal, not a new obligation, and the compiler itself
        refuses an event carrying a `linked_loop_id`.
        """
        if self._compiler is None:
            result.errors.append("no compiler configured; new loop not created")
            return

        try:
            graph = await self._compiler.compile(
                CompileGoalRequest(
                    user_id=self._user_id,
                    source_event=event,
                    available_apps=self._available_apps,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad compile must not kill the poller
            result.errors.append(f"compile failed: {exc}")
            logger.exception("compile failed for event %s", event.id)
            return

        loop_row = _plain(graph.loop.model_dump())
        loop_row.update(
            assumptions=list(graph.assumptions),
            clarification_needed=graph.clarification_needed,
            clarification_question=graph.clarification_question,
        )

        try:
            loop_id = await db.persist_compiled_graph(
                loop=loop_row,
                nodes=[_plain(node.model_dump()) for node in graph.nodes],
                edges=[_plain(edge.model_dump()) for edge in graph.edges],
                evidence_requirements=[
                    _plain(requirement.model_dump())
                    for requirement in graph.evidence_requirements
                ],
                actions=[_plain(action.model_dump()) for action in graph.proposed_actions],
                source_event_ids=[event.id],
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"persist failed: {exc}")
            logger.exception("persisting compiled graph failed for event %s", event.id)
            return

        result.compiled_loop_id = loop_id
        await queries.link_event_to_loop(event.id, loop_id)

    # --- existing loops ----------------------------------------------------

    async def _apply_outcome(
        self, event: Event, outcome: LoopOutcome, result: RuntimeResult
    ) -> None:
        """Record what verification concluded about one matched loop."""
        await queries.record_source_event(outcome.loop_id, event.id)

        if outcome.verification is None:
            # Routing matched but verification could not run. The match is still real,
            # so the provenance above is kept; there is simply nothing to assert yet.
            if outcome.error:
                result.errors.append(outcome.error)
            return

        operations: list[dict[str, Any]] = []
        for decision in outcome.verification.decisions:
            try:
                await queries.insert_evidence(
                    node_id=decision.node_id,
                    event_id=event.id,
                    relationship=_plain(decision.relationship),
                    confidence=decision.confidence,
                    reason=decision.reason,
                    extracted_fields=_plain(decision.extracted_fields),
                    verified=decision.evidence_satisfies_requirement,
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"{outcome.loop_id}: evidence write failed: {exc}")
                logger.exception("evidence write failed for event %s", event.id)
                continue

            result.evidence_written += 1
            if decision.evidence_satisfies_requirement:
                operations.append(
                    {
                        "type": VERIFY_NODE,
                        "target_id": decision.node_id,
                        "reason": decision.reason,
                    }
                )

        if operations:
            await db.apply_graph_operations(outcome.loop_id, operations)
            result.nodes_verified += len(operations)

        if outcome.requires_replan:
            await self._replan(event, outcome, result)

    async def _replan(
        self, event: Event, outcome: LoopOutcome, result: RuntimeResult
    ) -> None:
        """Repair a loop whose plan the evidence just invalidated."""
        if self._replanner is None:
            result.errors.append(f"{outcome.loop_id}: replan needed, no replanner configured")
            return

        rows = await queries.load_loop_graph(self._user_id, outcome.loop_id)
        if rows is None:
            result.errors.append(f"{outcome.loop_id}: vanished before replan")
            return

        try:
            # Reuse the hydrator rather than rebuilding the DTOs here: it derives
            # depends_on from the edges and is the single place that logic lives.
            hydrated = build_verify_request(rows, event)
        except HydrationError as exc:
            result.errors.append(f"{outcome.loop_id}: {exc}")
            return

        context_rows = await queries.load_replan_context_rows(outcome.loop_id)
        request = ReplanRequest(
            loop=hydrated.loop,
            nodes=hydrated.nodes,
            edges=[Edge.model_validate(row) for row in rows["edges"]],
            triggering_event=event,
            evidence_decisions=list(outcome.verification.decisions),
        )
        context = ReplanContext(
            # updated_at moves on every write via the loops trigger, so it identifies
            # the graph state this repair was computed against.
            state_revision=hydrated.loop.updated_at.isoformat(),
            requirements=[
                EvidenceRequirement.model_validate(row)
                for row in context_rows["requirements"]
            ],
            actions=[Action.model_validate(row) for row in context_rows["actions"]],
            evidence=[Evidence.model_validate(row) for row in context_rows["evidence"]],
            available_apps=self._available_apps,
            applied_event_ids=await queries.applied_event_ids(outcome.loop_id),
        )

        try:
            response = await self._replanner.replan(request, context=context)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{outcome.loop_id}: replan failed: {exc}")
            logger.exception("replan failed for loop %s", outcome.loop_id)
            return

        if not response.operations and not response.proposed_actions:
            return

        try:
            if response.operations:
                await db.apply_graph_operations(
                    outcome.loop_id,
                    [_plain(operation.model_dump()) for operation in response.operations],
                )
            for action in response.proposed_actions:
                await db.create_action(_plain(action.model_dump()))
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{outcome.loop_id}: repair write failed: {exc}")
            logger.exception("applying repair failed for loop %s", outcome.loop_id)
            return

        result.repaired_loop_ids = (*result.repaired_loop_ids, outcome.loop_id)
