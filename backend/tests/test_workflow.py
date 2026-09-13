"""End-to-end tests for the LangGraph event workflow.

No database, no network, no model: the store is `InMemoryRuntimeStore` and every agent is
a stub, so the whole pipeline is exercised for real while staying deterministic.
"""

import json
from datetime import UTC, datetime

import pytest
from conftest import FIXTURES
from fixtures.router import ACTING_USER_ID, load_event, load_loop_rows

from app.events.hydration import FixtureLoopGraphSource
from app.events.repository import FixtureLoopRepository, UnavailableLLM
from app.events.router import EventRouter
from app.graph.schemas import (
    CompiledGraph,
    GraphOperation,
    NodeEvidenceDecision,
    ReplanResponse,
    VerifyEventResponse,
)
from app.graph.state import InMemoryRuntimeStore
from app.graph.workflow import (
    ACTIVITY_ROUTED,
    ACTIVITY_VERIFIED,
    COMPILE,
    FINALIZE,
    REPLAN,
    ROUTE,
    VERIFY,
    EventWorkflow,
)

NOW = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)


def graph_rows(name: str = "refund") -> dict:
    graph = json.loads((FIXTURES / f"{name}_compiled_graph.json").read_text(encoding="utf-8"))
    return {
        "loop": graph["loop"],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "requirements": graph["evidence_requirements"],
        "actions": [],
        "evidence": [],
        "state_revision": "rev-1",
    }


class StubVerifier:
    def __init__(self, requires_replan: bool = False, relationship: str = "PARTIALLY_SUPPORTS"):
        self.requests: list = []
        self._requires_replan = requires_replan
        self._relationship = relationship

    async def verify(self, request) -> VerifyEventResponse:
        self.requests.append(request)
        return VerifyEventResponse(
            decisions=[
                NodeEvidenceDecision(
                    node_id=node.id,
                    relationship=self._relationship,
                    confidence=0.7,
                    reason="stubbed assessment",
                )
                for node in request.nodes
            ],
            requires_replan=self._requires_replan,
        )


class StubReplanner:
    def __init__(self) -> None:
        self.calls: list = []

    async def replan(self, request, *, context) -> ReplanResponse:
        self.calls.append((request, context))
        return ReplanResponse(
            operations=[
                GraphOperation(type="UPDATE_DEADLINE", target_id=request.nodes[0].id,
                               reason="stubbed repair")
            ],
            proposed_actions=[],
            summary="stubbed repair",
        )


class StubCompiler:
    def __init__(self, graph_name: str = "promise") -> None:
        self.calls: list = []
        self._graph = CompiledGraph.model_validate(
            json.loads((FIXTURES / f"{graph_name}_compiled_graph.json").read_text("utf-8"))
        )

    async def compile(self, request) -> CompiledGraph:
        self.calls.append(request)
        return self._graph


def build_workflow(store=None, verifier=None, replanner=None, compiler=None, graphs=None):
    router = EventRouter(
        repo=FixtureLoopRepository(load_loop_rows()),
        llm=UnavailableLLM(),
        user_id=ACTING_USER_ID,
        now_fn=lambda: NOW,
    )
    return EventWorkflow(
        router=router,
        graphs=graphs if graphs is not None else FixtureLoopGraphSource([graph_rows()]),
        store=store if store is not None else InMemoryRuntimeStore(),
        user_id=ACTING_USER_ID,
        verifier=verifier,
        replanner=replanner,
        compiler=compiler,
        available_apps=["gmail", "slack", "google_drive", "google_calendar", "loopgraph"],
    )


# --------------------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------------------


async def test_matched_event_is_ingested_routed_verified_and_finalized() -> None:
    store = InMemoryRuntimeStore()
    verifier = StubVerifier()
    workflow = build_workflow(store=store, verifier=verifier)

    state = await workflow.run(load_event("refund_confirmed_no_thread"))

    assert state["duplicate"] is False
    assert [m.loop_id for m in state["matches"]] == ["loop_refund_001"]
    assert "loop_refund_001" in state["verifications"]
    assert state["errors"] == []
    assert store.call_names == [
        "is_duplicate",
        "save_event",
        "link_event_to_loop",
        "log_activity",
        "save_evidence",
        "log_activity",
        "mark_processed",
    ]


async def test_verifier_receives_a_fully_hydrated_request() -> None:
    """The graph the verifier sees must be rebuilt from edges, not from stale row arrays."""
    from app.graph.evidence_validation import validate_verifier_request

    verifier = StubVerifier()
    await build_workflow(verifier=verifier).run(load_event("refund_confirmed_no_thread"))

    assert len(verifier.requests) == 1
    validate_verifier_request(verifier.requests[0])


async def test_routing_writes_linked_loop_id() -> None:
    """The router's thread signal reads events.linked_loop_id; without this write it is
    always NULL and thread recall is zero."""
    store = InMemoryRuntimeStore()
    event = load_event("refund_confirmed_no_thread")

    await build_workflow(store=store, verifier=StubVerifier()).run(event)

    assert store.links == {event.id: "loop_refund_001"}


async def test_every_decision_is_persisted_including_unfavourable_ones() -> None:
    store = InMemoryRuntimeStore()
    await build_workflow(store=store, verifier=StubVerifier(relationship="UNRELATED")).run(
        load_event("refund_confirmed_no_thread")
    )

    decisions = store.evidence["loop_refund_001"]
    assert decisions and all(d.relationship == "UNRELATED" for d in decisions)


async def test_activity_is_logged_for_routing_and_verification() -> None:
    store = InMemoryRuntimeStore()
    await build_workflow(store=store, verifier=StubVerifier()).run(
        load_event("refund_confirmed_no_thread")
    )

    kinds = {activity_type for _, activity_type, _ in store.activity}
    assert {ACTIVITY_ROUTED, ACTIVITY_VERIFIED} <= kinds


# --------------------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------------------


async def test_duplicate_event_stops_before_routing() -> None:
    store = InMemoryRuntimeStore()
    verifier = StubVerifier()
    workflow = build_workflow(store=store, verifier=verifier)
    event = load_event("refund_confirmed_no_thread")

    await workflow.run(event)
    store.calls.clear()
    second = await workflow.run(event)

    assert second["duplicate"] is True
    assert second.get("matches") == []
    assert store.call_names == ["is_duplicate"], "a replay must do no work at all"
    assert len(verifier.requests) == 1, "the second pass must not re-verify"


async def test_duplicate_is_not_marked_processed_twice() -> None:
    store = InMemoryRuntimeStore()
    workflow = build_workflow(store=store, verifier=StubVerifier())
    event = load_event("refund_confirmed_no_thread")

    await workflow.run(event)
    await workflow.run(event)

    assert store.processed == [event.id]


async def test_an_edited_message_is_new_work_not_a_duplicate() -> None:
    """Event.dedup_key folds in the item's version stamp, so an edit is not a replay."""
    store = InMemoryRuntimeStore()
    workflow = build_workflow(store=store, verifier=StubVerifier())
    original = load_event("refund_confirmed_no_thread")
    edited = original.model_copy(update={"metadata": {"version": "2"}})

    await workflow.run(original)
    await workflow.run(edited)

    assert store.processed == [original.id, edited.id]


# --------------------------------------------------------------------------------------
# Replanning
# --------------------------------------------------------------------------------------


async def test_requires_replan_reaches_the_replanner_and_applies_operations() -> None:
    store = InMemoryRuntimeStore()
    replanner = StubReplanner()
    await build_workflow(
        store=store,
        verifier=StubVerifier(requires_replan=True, relationship="CONTRADICTS"),
        replanner=replanner,
    ).run(load_event("refund_confirmed_no_thread"))

    assert len(replanner.calls) == 1
    assert store.operations["loop_refund_001"]
    assert "apply_operations" in store.call_names


async def test_replan_context_carries_the_available_apps_and_revision() -> None:
    replanner = StubReplanner()
    await build_workflow(
        verifier=StubVerifier(requires_replan=True, relationship="CONTRADICTS"),
        replanner=replanner,
    ).run(load_event("refund_confirmed_no_thread"))

    _request, context = replanner.calls[0]
    assert context.state_revision == "rev-1"
    assert "gmail" in context.available_apps


async def test_no_replan_when_verification_did_not_ask_for_one() -> None:
    replanner = StubReplanner()
    await build_workflow(verifier=StubVerifier(requires_replan=False), replanner=replanner).run(
        load_event("refund_confirmed_no_thread")
    )

    assert replanner.calls == []


# --------------------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------------------


async def test_unmatched_new_obligation_reaches_the_compiler() -> None:
    """No deterministic match and no semantic stage configured means the router returns
    nothing, so this drives compile through an explicit create_new_loop state."""
    store = InMemoryRuntimeStore()
    compiler = StubCompiler()
    workflow = build_workflow(store=store, compiler=compiler)

    # Route the new_obligation fixture with a stub that declares a new obligation.
    from provider_helpers import FakeProvider

    from app.events.router_models import RouteDraft

    workflow._router._llm = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[],
                is_new_obligation=True,
                obligation_reason="Bank requires proof of address by September 30.",
            )
        ]
    )
    state = await workflow.run(load_event("new_obligation"))

    assert state["matches"] == []
    assert state["create_new_loop"] is True
    assert len(compiler.calls) == 1
    assert state["compiled_loop_id"] == compiler._graph.loop.id
    assert store.graphs


async def test_compiling_a_loop_links_its_source_event() -> None:
    """The creating event is the loop's source event, so it must be linked too.

    Without this the next message in the same thread has nothing to match against:
    find_loop_ids_by_thread joins on events.linked_loop_id, so a loop whose only event
    is unlinked is invisible to the cheapest routing signal.
    """
    store = InMemoryRuntimeStore()
    compiler = StubCompiler()
    workflow = build_workflow(store=store, compiler=compiler)

    from provider_helpers import FakeProvider

    from app.events.router_models import RouteDraft

    workflow._router._llm = FakeProvider(
        outputs=[RouteDraft(candidates=[], is_new_obligation=True, obligation_reason="owed")]
    )
    event = load_event("new_obligation")
    await workflow.run(event)

    assert store.links == {event.id: compiler._graph.loop.id}
    assert "link_event_to_loop" in store.call_names


async def test_compiler_is_never_given_a_linked_event() -> None:
    """A3 raises CompilerInputError for a linked event. The workflow refuses first."""
    compiler = StubCompiler()
    workflow = build_workflow(compiler=compiler)

    state = await workflow._compile(
        {"event": load_event("deadline_reached"), "user_id": ACTING_USER_ID}
    )

    assert compiler.calls == []
    assert any("already linked" in error for error in state["errors"])


async def test_matched_event_never_reaches_the_compiler() -> None:
    """An event that belongs to a tracked goal is not also a new obligation."""
    compiler = StubCompiler()
    await build_workflow(verifier=StubVerifier(), compiler=compiler).run(
        load_event("refund_confirmed_no_thread")
    )

    assert compiler.calls == []


# --------------------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------------------


async def test_unloadable_loop_records_an_error_without_crashing() -> None:
    store = InMemoryRuntimeStore()
    state = await build_workflow(
        store=store, verifier=StubVerifier(), graphs=FixtureLoopGraphSource([])
    ).run(load_event("refund_confirmed_no_thread"))

    assert state["verifications"] == {}
    assert any("not loadable" in error for error in state["errors"])
    # Still finalized: the event was seen and must not be replayed forever.
    assert store.processed


async def test_missing_verifier_degrades_to_an_error() -> None:
    state = await build_workflow(verifier=None).run(load_event("refund_confirmed_no_thread"))
    assert any("no verifier configured" in error for error in state["errors"])


async def test_event_matching_nothing_finalizes_without_writes() -> None:
    store = InMemoryRuntimeStore()
    workflow = build_workflow(store=store)

    from provider_helpers import FakeProvider

    from app.events.router_models import RouteDraft

    workflow._router._llm = FakeProvider(
        outputs=[RouteDraft(candidates=[], is_new_obligation=False, obligation_reason=None)]
    )
    state = await workflow.run(load_event("newsletter"))

    assert state["matches"] == []
    assert state["create_new_loop"] is False
    assert "save_evidence" not in store.call_names
    assert "save_compiled_graph" not in store.call_names
    assert store.processed


# --------------------------------------------------------------------------------------
# Graph shape and boundaries
# --------------------------------------------------------------------------------------


def test_graph_has_the_expected_nodes() -> None:
    workflow = build_workflow()
    nodes = set(workflow.app.get_graph().nodes)
    assert {ROUTE, VERIFY, REPLAN, COMPILE, FINALIZE} <= nodes


def test_workflow_never_imports_the_write_layer_directly() -> None:
    """Every write goes through RuntimeStore, so the graph runs with no database."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "app" / "graph" / "workflow.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(module.startswith("app.db") for module in imported)


def test_in_memory_store_satisfies_the_protocol() -> None:
    from app.graph.state import RuntimeStore

    assert isinstance(InMemoryRuntimeStore(), RuntimeStore)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"matches": [object()], "create_new_loop": False}, VERIFY),
        ({"matches": [], "create_new_loop": True}, COMPILE),
        ({"matches": [], "create_new_loop": False}, FINALIZE),
    ],
)
def test_routing_branch_is_exhaustive(state, expected) -> None:
    assert EventWorkflow._after_route(state) == expected
