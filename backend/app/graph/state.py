"""Shared state and the persistence boundary for the LangGraph event workflow.

Every write the runtime performs goes through `RuntimeStore`. Nothing in the workflow
imports `app.db`, so the whole pipeline runs and is tested against `InMemoryRuntimeStore`
without asyncpg, a live Supabase, or a network. Swapping in the real adapter is a
constructor argument, not a rewrite.

The state is a `TypedDict` because LangGraph merges partial updates returned by each node.
`activity` and `errors` use `operator.add` reducers so nodes append rather than clobber;
everything else is last-write-wins, which is safe because the graph is linear.
"""

import operator
from collections.abc import Sequence
from typing import Annotated, Any, Protocol, TypedDict, runtime_checkable

from app.graph.schemas import (
    CompiledGraph,
    Event,
    GraphOperation,
    LoopMatch,
    NodeEvidenceDecision,
    ReplanResponse,
    VerifyEventResponse,
)


class PipelineState(TypedDict, total=False):
    """One event's journey through the graph.

    `user_id` is carried explicitly because `Event` has no user field — the same contract
    gap the router works around. Every tenant-scoped call downstream reads it from here.
    """

    event: Event
    user_id: str

    # Ingestion
    duplicate: bool

    # Routing
    matches: list[LoopMatch]
    create_new_loop: bool

    # Verification, keyed by loop id: one VerifyEventRequest is single-loop, so a shared
    # thread matching two loops produces two independent assessments.
    verifications: dict[str, VerifyEventResponse]

    # Replanning, keyed by loop id. Only loops whose verification set requires_replan.
    replans: dict[str, ReplanResponse]

    # Compilation, when the event was judged a new obligation.
    compiled_loop_id: str | None

    activity: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


def initial_state(event: Event, user_id: str) -> PipelineState:
    """A fresh state. Collections start empty so every node can append safely."""
    return PipelineState(
        event=event,
        user_id=user_id,
        duplicate=False,
        matches=[],
        create_new_loop=False,
        verifications={},
        replans={},
        compiled_loop_id=None,
        activity=[],
        errors=[],
    )


@runtime_checkable
class RuntimeStore(Protocol):
    """Every write the pipeline performs. Implementations own transactionality.

    Deliberately narrow: the workflow decides *what* should happen, the store decides
    *how* it is committed. A Postgres implementation is free to batch these into one
    transaction with a compare-and-swap on the loop's revision; the in-memory one just
    records them.
    """

    async def is_duplicate(self, dedup_key: str) -> bool:
        """True when this exact real-world occurrence has already been ingested.

        Backed by `uq_event_external(source_app, external_id)` plus the version stamp in
        `Event.dedup_key`, so an edited message counts as new rather than a replay.
        """
        ...

    async def save_event(self, event: Event) -> None: ...

    async def link_event_to_loop(self, event_id: str, loop_id: str) -> None:
        """Set `events.linked_loop_id` after routing resolves it.

        This is the write that makes the router's thread signal work at all: the signal
        reads `events.linked_loop_id`, so without this the column stays NULL forever and
        thread-based recall is zero. Only called for an unambiguous single match, because
        the column holds one id — a shared thread matching several loops belongs in
        `loop_source_events` instead.
        """
        ...

    async def save_evidence(
        self, loop_id: str, event_id: str, decisions: Sequence[NodeEvidenceDecision]
    ) -> None:
        """Persist assessed evidence for every decision, including INSUFFICIENT.

        `Evidence.verified` records that the assessment happened, never that the node is
        complete. Completion gates are the runtime's, applied separately.
        """
        ...

    async def apply_operations(
        self, loop_id: str, operations: Sequence[GraphOperation]
    ) -> None: ...

    async def save_compiled_graph(self, graph: CompiledGraph) -> None: ...

    async def mark_processed(self, event_id: str) -> None: ...

    async def log_activity(self, loop_id: str, activity_type: str, message: str) -> None: ...


class InMemoryRuntimeStore:
    """A `RuntimeStore` that records instead of persisting.

    Makes the whole workflow runnable and assertable with no database. Every call is
    appended to `calls` in order, so a test can assert not just the final state but the
    sequence of writes — which is where ordering bugs actually live.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.seen: set[str] = set()
        self.events: dict[str, Event] = {}
        self.links: dict[str, str] = {}
        self.evidence: dict[str, list[NodeEvidenceDecision]] = {}
        self.operations: dict[str, list[GraphOperation]] = {}
        self.graphs: list[CompiledGraph] = []
        self.processed: list[str] = []
        self.activity: list[tuple[str, str, str]] = []

    def _record(self, name: str, **fields: Any) -> None:
        self.calls.append((name, fields))

    @property
    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def is_duplicate(self, dedup_key: str) -> bool:
        self._record("is_duplicate", dedup_key=dedup_key)
        return dedup_key in self.seen

    async def save_event(self, event: Event) -> None:
        self._record("save_event", event_id=event.id)
        self.seen.add(event.dedup_key)
        self.events[event.id] = event

    async def link_event_to_loop(self, event_id: str, loop_id: str) -> None:
        self._record("link_event_to_loop", event_id=event_id, loop_id=loop_id)
        self.links[event_id] = loop_id

    async def save_evidence(
        self, loop_id: str, event_id: str, decisions: Sequence[NodeEvidenceDecision]
    ) -> None:
        self._record("save_evidence", loop_id=loop_id, event_id=event_id, count=len(decisions))
        self.evidence.setdefault(loop_id, []).extend(decisions)

    async def apply_operations(
        self, loop_id: str, operations: Sequence[GraphOperation]
    ) -> None:
        self._record("apply_operations", loop_id=loop_id, count=len(operations))
        self.operations.setdefault(loop_id, []).extend(operations)

    async def save_compiled_graph(self, graph: CompiledGraph) -> None:
        self._record("save_compiled_graph", loop_id=graph.loop.id)
        self.graphs.append(graph)

    async def mark_processed(self, event_id: str) -> None:
        self._record("mark_processed", event_id=event_id)
        self.processed.append(event_id)

    async def log_activity(self, loop_id: str, activity_type: str, message: str) -> None:
        self._record("log_activity", loop_id=loop_id, activity_type=activity_type)
        self.activity.append((loop_id, activity_type, message))
