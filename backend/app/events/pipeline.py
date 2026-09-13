"""Compose routing and verification into one pass over an event, and return a plan.

`EventPipeline.process` answers "what should the runtime do about this event" without
doing any of it. It performs no writes, opens no transaction, marks nothing processed,
persists no evidence, and never mutates a loop or node. It returns an `EventPlan` for the
runtime to apply inside its own transaction, which keeps the read side testable and leaves
the compare-and-swap, dedup and activity-logging concerns where they belong.

The order is fixed by the contracts, not by preference:

  * A pre-linked event is already routed, so Stage 0 returns it and nothing re-derives it.
  * An event that matched a loop must NOT reach the compiler. `compiler.py` raises
    `CompilerInputError` when `source_event.linked_loop_id` is set, and more importantly a
    matched event is evidence about an existing goal, not a new one.
  * Verification runs per matched loop, because `VerifyEventRequest` is single-loop. A
    thread shared by two loops produces two verifications, not one merged answer.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.events.hydration import HydrationError, LoopGraphSource, build_verify_request
from app.events.router import EventRouter
from app.graph.schemas import (
    Event,
    LoopMatch,
    RouteEventRequest,
    VerifyEventRequest,
    VerifyEventResponse,
)


@dataclass(frozen=True)
class LoopOutcome:
    """What verification concluded about one matched loop."""

    loop_id: str
    match: LoopMatch
    verification: VerifyEventResponse | None
    error: str | None = None

    @property
    def requires_replan(self) -> bool:
        return bool(self.verification and self.verification.requires_replan)


@dataclass(frozen=True)
class EventPlan:
    """The runtime's to-do list for one event. Describes intent; performs nothing.

    `compile_new_loop` is only ever true when `outcomes` is empty — an event that belongs
    to an existing goal is not also a new obligation.
    """

    event_id: str
    outcomes: tuple[LoopOutcome, ...] = ()
    compile_new_loop: bool = False
    skipped: tuple[str, ...] = field(default=())

    @property
    def matched_loop_ids(self) -> tuple[str, ...]:
        return tuple(outcome.loop_id for outcome in self.outcomes)

    @property
    def replan_loop_ids(self) -> tuple[str, ...]:
        return tuple(o.loop_id for o in self.outcomes if o.requires_replan)

    @property
    def link_loop_id(self) -> str | None:
        """The value the runtime should write to `events.linked_loop_id`, if any.

        Only set when exactly one loop matched. A shared thread legitimately matches
        several loops, and the column holds one id, so provenance for the ambiguous case
        belongs in `loop_source_events` instead — the schema review is explicit that
        `linked_loop_id` is a routing hint, not the provenance relation.
        """
        ids = self.matched_loop_ids
        return ids[0] if len(ids) == 1 else None


class VerifierService:
    """The subset of `EvidenceVerifier` this pipeline needs."""

    async def verify(self, request: VerifyEventRequest) -> VerifyEventResponse: ...


class EventPipeline:
    """Routes one event, then verifies it against each loop it matched.

    `user_id` is threaded through from the router for the same reason the router takes it:
    `Event` has no user field, so tenancy cannot be inferred and must be supplied. The
    graph source re-applies it, so a routing bug cannot leak another tenant's graph into a
    verification request.
    """

    def __init__(
        self,
        router: EventRouter,
        graphs: LoopGraphSource,
        verifier: VerifierService | None,
        user_id: str,
    ) -> None:
        self._router = router
        self._graphs = graphs
        self._verifier = verifier
        self._user_id = user_id

    async def process(self, event: Event) -> EventPlan:
        routed = await self._router.route(RouteEventRequest(event=event))

        if not routed.matches:
            return EventPlan(
                event_id=event.id,
                outcomes=(),
                compile_new_loop=routed.create_new_loop_candidate,
            )

        outcomes: list[LoopOutcome] = []
        skipped: list[str] = []
        for match in routed.matches:
            outcome, skip = await self._verify_one(event, match)
            outcomes.append(outcome)
            if skip:
                skipped.append(skip)

        return EventPlan(
            event_id=event.id,
            outcomes=tuple(outcomes),
            # Never both. The router already enforces this; restated here because the
            # consequence of getting it wrong is a duplicate loop for a tracked goal.
            compile_new_loop=False,
            skipped=tuple(skipped),
        )

    async def _verify_one(self, event: Event, match: LoopMatch) -> tuple[LoopOutcome, str | None]:
        """Hydrate and verify one matched loop.

        A failure here degrades that one loop to an unverified match rather than failing
        the event: one unloadable loop must not stop the others being assessed.
        """
        rows = await self._graphs.load_loop_graph(self._user_id, match.loop_id)
        if rows is None:
            reason = f"{match.loop_id}: not loadable for this user"
            return LoopOutcome(match.loop_id, match, None, reason), reason

        try:
            request = build_verify_request(rows, event)
        except HydrationError as exc:
            reason = f"{match.loop_id}: {exc}"
            return LoopOutcome(match.loop_id, match, None, reason), reason

        if self._verifier is None:
            reason = f"{match.loop_id}: no verifier configured"
            return LoopOutcome(match.loop_id, match, None, reason), reason

        verification = await self._verifier.verify(request)
        return LoopOutcome(match.loop_id, match, verification), None


def candidate_node_ids(rows: Sequence[Any]) -> list[str]:
    """Every node id in a loaded graph, for callers narrowing the candidate set."""
    return [node["id"] for node in rows]
