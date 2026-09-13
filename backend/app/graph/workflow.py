"""The LangGraph event workflow: one normalized Event through the whole pipeline.

    ingest -> route -> verify -> replan -> finalize
                    \\-> compile ------------/

Node order is fixed by the contracts, not by taste:

  * **ingest** deduplicates before anything expensive. `Event.dedup_key` folds in the
    item's own version stamp, so an edited message is new work while a re-delivered
    webhook is not.
  * **route** decides which loops the event touches. It is the only stage allowed to be
    wrong cheaply — a wrong match is corrected by the verifier returning UNRELATED.
  * **verify** runs per matched loop, because `VerifyEventRequest` is single-loop.
  * **compile** only ever runs when routing matched nothing. `compiler.py` raises
    `CompilerInputError` for an event that is already linked, and more fundamentally an
    event belonging to a tracked goal is not a new obligation.
  * **replan** runs only for loops whose verification asked for it. A5 requests repairs;
    it never verifies, and VERIFY_NODE from a planner is rejected upstream.
  * **finalize** marks the event processed last, so a crash mid-pipeline leaves it
    unprocessed and replayable rather than silently consumed.

Every write goes through `RuntimeStore`, so this module never imports `app.db` and the
whole graph runs against `InMemoryRuntimeStore` with no database.
"""

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from app.events.hydration import HydrationError, LoopGraphSource, build_verify_request
from app.events.router import EventRouter
from app.graph.repair_schemas import ReplanContext
from app.graph.schemas import (
    CompiledGraph,
    CompileGoalRequest,
    Event,
    ReplanRequest,
    ReplanResponse,
    RouteEventRequest,
    VerifyEventRequest,
    VerifyEventResponse,
)
from app.graph.state import PipelineState, RuntimeStore, initial_state

logger = logging.getLogger(__name__)

# Node names. Referenced by edges and asserted in tests, so they are constants rather
# than repeated string literals.
INGEST = "ingest"
ROUTE = "route"
VERIFY = "verify"
REPLAN = "replan"
COMPILE = "compile"
FINALIZE = "finalize"

# Activity log vocabulary. The runtime writes one coherent entry per meaningful decision.
ACTIVITY_ROUTED = "EVENT_ROUTED"
ACTIVITY_VERIFIED = "EVENT_VERIFIED"
ACTIVITY_REPLANNED = "LOOP_REPLANNED"
ACTIVITY_COMPILED = "LOOP_COMPILED"

# A loop loaded for replanning has no prior revision in memory; the real store overwrites
# this with the revision it read, and uses it for a compare-and-swap on write.
UNKNOWN_REVISION = "unknown"


class VerifierService(Protocol):
    async def verify(self, request: VerifyEventRequest) -> VerifyEventResponse: ...


class ReplannerService(Protocol):
    async def replan(
        self, request: ReplanRequest, *, context: ReplanContext
    ) -> ReplanResponse: ...


class CompilerService(Protocol):
    async def compile(self, request: CompileGoalRequest) -> CompiledGraph: ...


def _rows_to_models(rows: dict[str, Any], key: str, model: type) -> list[Any]:
    return [model.model_validate(row) for row in rows.get(key) or []]


class EventWorkflow:
    """Builds and runs the compiled LangGraph.

    Services are injected as protocols so the graph can be exercised end to end with
    stubs. `available_apps` is passed through to the replanner, which rejects a repair
    proposing an action on an app that is not actually connected.
    """

    def __init__(
        self,
        router: EventRouter,
        graphs: LoopGraphSource,
        store: RuntimeStore,
        user_id: str,
        verifier: VerifierService | None = None,
        replanner: ReplannerService | None = None,
        compiler: CompilerService | None = None,
        available_apps: Sequence[str] = (),
    ) -> None:
        self._router = router
        self._graphs = graphs
        self._store = store
        self._user_id = user_id
        self._verifier = verifier
        self._replanner = replanner
        self._compiler = compiler
        self._available_apps = list(available_apps)
        self._app = self._build().compile()

    # -- graph shape ---------------------------------------------------------------

    def _build(self) -> StateGraph:
        graph = StateGraph(PipelineState)
        graph.add_node(INGEST, self._ingest)
        graph.add_node(ROUTE, self._route)
        graph.add_node(VERIFY, self._verify)
        graph.add_node(REPLAN, self._replan)
        graph.add_node(COMPILE, self._compile)
        graph.add_node(FINALIZE, self._finalize)

        graph.add_edge(START, INGEST)
        # A duplicate stops immediately: it must not be re-routed, re-verified, or
        # re-marked, and it must not produce a second set of evidence rows.
        graph.add_conditional_edges(
            INGEST, self._after_ingest, {ROUTE: ROUTE, END: END}
        )
        graph.add_conditional_edges(
            ROUTE, self._after_route, {VERIFY: VERIFY, COMPILE: COMPILE, FINALIZE: FINALIZE}
        )
        graph.add_conditional_edges(
            VERIFY, self._after_verify, {REPLAN: REPLAN, FINALIZE: FINALIZE}
        )
        graph.add_edge(REPLAN, FINALIZE)
        graph.add_edge(COMPILE, FINALIZE)
        graph.add_edge(FINALIZE, END)
        return graph

    @staticmethod
    def _after_ingest(state: PipelineState) -> str:
        return END if state.get("duplicate") else ROUTE

    @staticmethod
    def _after_route(state: PipelineState) -> str:
        if state.get("matches"):
            return VERIFY
        return COMPILE if state.get("create_new_loop") else FINALIZE

    @staticmethod
    def _after_verify(state: PipelineState) -> str:
        wants_replan = any(
            response.requires_replan for response in state.get("verifications", {}).values()
        )
        return REPLAN if wants_replan else FINALIZE

    # -- nodes ---------------------------------------------------------------------

    async def _ingest(self, state: PipelineState) -> PipelineState:
        event = state["event"]
        if await self._store.is_duplicate(event.dedup_key):
            return PipelineState(
                duplicate=True, activity=[f"duplicate {event.dedup_key}"], errors=[]
            )
        await self._store.save_event(event)
        return PipelineState(duplicate=False, activity=[f"ingested {event.id}"], errors=[])

    async def _route(self, state: PipelineState) -> PipelineState:
        routed = await self._router.route(RouteEventRequest(event=state["event"]))
        matches = list(routed.matches)

        # The write that keeps the router's thread signal alive. Only for an unambiguous
        # match, because events.linked_loop_id holds exactly one id.
        if len(matches) == 1:
            await self._store.link_event_to_loop(state["event"].id, matches[0].loop_id)
            await self._store.log_activity(
                matches[0].loop_id, ACTIVITY_ROUTED, matches[0].reason
            )

        return PipelineState(
            matches=matches,
            create_new_loop=routed.create_new_loop_candidate,
            activity=[f"routed to {[m.loop_id for m in matches]}"],
            errors=[],
        )

    async def _verify(self, state: PipelineState) -> PipelineState:
        verifications: dict[str, VerifyEventResponse] = {}
        errors: list[str] = []
        activity: list[str] = []

        for match in state["matches"]:
            rows = await self._graphs.load_loop_graph(self._user_id, match.loop_id)
            if rows is None:
                errors.append(f"{match.loop_id}: not loadable for this user")
                continue
            try:
                request = build_verify_request(rows, state["event"])
            except HydrationError as exc:
                errors.append(f"{match.loop_id}: {exc}")
                continue
            if self._verifier is None:
                errors.append(f"{match.loop_id}: no verifier configured")
                continue

            response = await self._verifier.verify(request)
            verifications[match.loop_id] = response
            # Every decision is persisted, including UNRELATED and INSUFFICIENT: the
            # assessment is the record, not just the favourable ones.
            await self._store.save_evidence(
                match.loop_id, state["event"].id, response.decisions
            )
            await self._store.log_activity(
                match.loop_id,
                ACTIVITY_VERIFIED,
                f"{len(response.decisions)} decisions for event {state['event'].id}",
            )
            activity.append(f"verified {match.loop_id}")

        return PipelineState(verifications=verifications, activity=activity, errors=errors)

    async def _replan(self, state: PipelineState) -> PipelineState:
        replans: dict[str, ReplanResponse] = {}
        errors: list[str] = []
        activity: list[str] = []

        for loop_id, verification in state["verifications"].items():
            if not verification.requires_replan:
                continue
            if self._replanner is None:
                errors.append(f"{loop_id}: no replanner configured")
                continue

            rows = await self._graphs.load_loop_graph(self._user_id, loop_id)
            if rows is None:
                errors.append(f"{loop_id}: not loadable for replanning")
                continue

            try:
                request, context = self._build_replan_input(rows, state["event"], verification)
                response = await self._replanner.replan(request, context=context)
            except (HydrationError, ValueError) as exc:
                # A rejected repair leaves the graph untouched, which is the safe outcome.
                errors.append(f"{loop_id}: replan rejected: {exc}")
                continue

            replans[loop_id] = response
            await self._store.apply_operations(loop_id, response.operations)
            await self._store.log_activity(loop_id, ACTIVITY_REPLANNED, response.summary)
            activity.append(f"replanned {loop_id} ({len(response.operations)} operations)")

        return PipelineState(replans=replans, activity=activity, errors=errors)

    def _build_replan_input(
        self, rows: dict[str, Any], event: Event, verification: VerifyEventResponse
    ) -> tuple[ReplanRequest, ReplanContext]:
        """Assemble A5's input from the same rows the verifier used.

        `ReplanRequest` alone is not enough to check a repair, so `ReplanContext` carries
        the requirements, actions and evidence. Both are built from one snapshot so the
        planner never sees a half-updated graph.
        """
        from app.graph.schemas import (
            Action,
            Edge,
            Evidence,
            EvidenceRequirement,
            Loop,
            OutcomeNode,
        )

        hydrated = build_verify_request(rows, event)
        request = ReplanRequest(
            loop=Loop.model_validate(hydrated.loop.model_dump()),
            nodes=[OutcomeNode.model_validate(node.model_dump()) for node in hydrated.nodes],
            edges=_rows_to_models(rows, "edges", Edge),
            triggering_event=event,
            evidence_decisions=list(verification.decisions),
        )
        context = ReplanContext(
            state_revision=str(rows.get("state_revision") or UNKNOWN_REVISION),
            requirements=_rows_to_models(rows, "requirements", EvidenceRequirement),
            actions=_rows_to_models(rows, "actions", Action),
            evidence=_rows_to_models(rows, "evidence", Evidence),
            available_apps=list(self._available_apps),
            applied_event_ids=list(rows.get("applied_event_ids") or []),
        )
        return request, context

    async def _compile(self, state: PipelineState) -> PipelineState:
        event = state["event"]
        if self._compiler is None:
            return PipelineState(errors=["no compiler configured"], activity=[])
        if event.linked_loop_id is not None:
            # A3 raises CompilerInputError for this. Catching it here keeps a routing bug
            # from turning into a spurious duplicate loop.
            return PipelineState(
                errors=[f"refusing to compile: event is already linked to {event.linked_loop_id}"],
                activity=[],
            )

        request = CompileGoalRequest(
            user_id=state["user_id"],
            source_event=event,
            available_apps=self._available_apps or ["loopgraph"],
        )
        graph = await self._compiler.compile(request)
        await self._store.save_compiled_graph(graph)
        # The event that created a loop is that loop's source event, so it gets linked
        # too. Without this the next message in the same thread has nothing to match
        # against and falls through to the model, and the cheapest signal never fires.
        # Safe to set now: A3 only refuses an event that arrives ALREADY linked.
        await self._store.link_event_to_loop(event.id, graph.loop.id)
        await self._store.log_activity(graph.loop.id, ACTIVITY_COMPILED, graph.loop.goal)
        return PipelineState(
            compiled_loop_id=graph.loop.id,
            activity=[f"compiled {graph.loop.id}"],
            errors=[],
        )

    async def _finalize(self, state: PipelineState) -> PipelineState:
        # Last, deliberately: a crash before this point leaves the event unprocessed and
        # therefore replayable, which is the recoverable failure mode.
        await self._store.mark_processed(state["event"].id)
        return PipelineState(activity=["finalized"], errors=[])

    # -- entry point ---------------------------------------------------------------

    async def run(self, event: Event) -> PipelineState:
        """Process one event and return the final state."""
        return await self._app.ainvoke(initial_state(event, self._user_id))

    @property
    def app(self):
        """The compiled LangGraph, for callers that want streaming or checkpointing."""
        return self._app
