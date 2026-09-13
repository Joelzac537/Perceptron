"""Tests for loop-graph hydration and the event pipeline.

The graph fixtures are real `CompiledGraph` payloads authored by the compiler's owner, so
hydration is checked against a graph this module did not invent.
"""

import json
from datetime import UTC, datetime

import pytest
from conftest import FIXTURES
from fixtures.router import ACTING_USER_ID, load_event, load_loop_rows

from app.events.hydration import (
    DERIVED_NODE_FIELDS,
    EVENT_ONLY_COLUMNS,
    FixtureLoopGraphSource,
    HydrationError,
    LoopGraphSource,
    build_event,
    build_verify_request,
)
from app.events.pipeline import EventPipeline, EventPlan
from app.events.repository import FixtureLoopRepository, UnavailableLLM
from app.events.router import EventRouter
from app.graph.evidence_validation import validate_verifier_request
from app.graph.schemas import NodeEvidenceDecision, VerifyEventResponse

NOW = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)


def graph_rows(name: str = "refund") -> dict:
    """A compiled-graph fixture reshaped as the rows a database would return."""
    graph = json.loads((FIXTURES / f"{name}_compiled_graph.json").read_text(encoding="utf-8"))
    return {
        "loop": graph["loop"],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "requirements": graph["evidence_requirements"],
    }


def unlinked_event():
    return load_event("refund_confirmed_no_thread")


# --------------------------------------------------------------------------------------
# Derived fields are rebuilt, not trusted
# --------------------------------------------------------------------------------------


def test_node_ids_are_rebuilt_from_the_node_rows() -> None:
    rows = graph_rows()
    rows["loop"]["node_ids"] = ["a_stale_lie"]

    request = build_verify_request(rows, unlinked_event())

    assert request.loop.node_ids == [node["id"] for node in rows["nodes"]]
    assert "a_stale_lie" not in request.loop.node_ids


def test_depends_on_is_rebuilt_from_depends_on_edges() -> None:
    """A DEPENDS_ON edge runs dependent -> prerequisite, so the source is the dependent."""
    rows = graph_rows()
    expected: dict[str, list[str]] = {node["id"]: [] for node in rows["nodes"]}
    for edge in rows["edges"]:
        if edge["relationship"] == "DEPENDS_ON":
            expected[edge["source_node_id"]].append(edge["target_node_id"])

    request = build_verify_request(rows, unlinked_event())

    assert {node.id: node.depends_on for node in request.nodes} == expected
    assert any(expected.values()), "the fixture must actually exercise a dependency"


def test_stale_depends_on_in_the_row_is_discarded() -> None:
    rows = graph_rows()
    for node in rows["nodes"]:
        node["depends_on"] = ["node_that_does_not_exist"]

    request = build_verify_request(rows, unlinked_event())

    for node in request.nodes:
        assert "node_that_does_not_exist" not in node.depends_on


def test_non_depends_on_edges_do_not_create_dependencies() -> None:
    rows = graph_rows()
    first, second = rows["nodes"][0]["id"], rows["nodes"][1]["id"]
    rows["edges"].append(
        {
            "id": "edge_proves_1",
            "loop_id": rows["loop"]["id"],
            "source_node_id": first,
            "target_node_id": second,
            "relationship": "PROVES",
            "reason": None,
            "created_at": rows["loop"]["created_at"],
        }
    )

    request = build_verify_request(rows, unlinked_event())
    by_id = {node.id: node for node in request.nodes}
    assert second not in by_id[first].depends_on


def test_requirement_ids_are_rebuilt_from_the_requirement_rows() -> None:
    rows = graph_rows()
    request = build_verify_request(rows, unlinked_event())

    expected = {
        node["id"]: [r["id"] for r in rows["requirements"] if r["node_id"] == node["id"]]
        for node in rows["nodes"]
    }
    assert {n.id: n.evidence_requirement_ids for n in request.nodes} == expected


def test_every_derived_field_is_listed_as_derived() -> None:
    """Guards against a new derived array being added to the DTO and silently trusted."""
    assert DERIVED_NODE_FIELDS == {
        "depends_on",
        "evidence_requirement_ids",
        "evidence_ids",
        "action_ids",
    }


# --------------------------------------------------------------------------------------
# The result satisfies the verifier's own input rules
# --------------------------------------------------------------------------------------


def test_hydrated_request_passes_the_verifiers_input_validation() -> None:
    """The real check: A4 rejects a malformed request before any model call."""
    request = build_verify_request(graph_rows(), unlinked_event())
    validate_verifier_request(request)


@pytest.mark.parametrize("name", ["refund", "promise", "renewal"])
def test_every_compiler_fixture_hydrates_and_validates(name: str) -> None:
    request = build_verify_request(graph_rows(name), unlinked_event())
    validate_verifier_request(request)


def test_narrowing_candidates_still_supplies_all_their_requirements() -> None:
    """A4 requires every requirement for each supplied node, not a subset."""
    rows = graph_rows()
    target = rows["nodes"][0]["id"]

    request = build_verify_request(rows, unlinked_event(), candidate_node_ids=[target])

    assert [node.id for node in request.nodes] == [target]
    expected = {r["id"] for r in rows["requirements"] if r["node_id"] == target}
    assert {r.id for r in request.requirements} == expected
    validate_verifier_request(request)


# --------------------------------------------------------------------------------------
# The events.created_at column
# --------------------------------------------------------------------------------------


def test_event_row_drops_the_sql_only_created_at_column() -> None:
    """The events table has created_at; the strict Event DTO forbids it."""
    row = json.loads(unlinked_event().model_dump_json())
    row["created_at"] = "2026-09-15T09:31:47-04:00"

    event = build_event(row)
    assert event.id == unlinked_event().id
    assert "created_at" not in EVENT_ONLY_COLUMNS - {"created_at"}


def test_unknown_columns_are_still_reported_rather_than_swallowed() -> None:
    """Only the documented SQL-only columns are dropped. Real drift must surface."""
    row = json.loads(unlinked_event().model_dump_json())
    row["some_new_column"] = "surprise"

    with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
        build_event(row)


# --------------------------------------------------------------------------------------
# Collect-then-raise validation
# --------------------------------------------------------------------------------------


def test_must_all_match_false_is_rejected_with_a_precise_reason() -> None:
    """A4 raises VerifierInputError for this. Failing here names the requirement."""
    rows = graph_rows()
    rows["requirements"][0]["must_all_match"] = False

    with pytest.raises(HydrationError) as error:
        build_verify_request(rows, unlinked_event())

    assert any("must_all_match=false" in issue for issue in error.value.issues)


def test_event_linked_to_a_different_loop_is_rejected() -> None:
    rows = graph_rows()
    event = load_event("deadline_reached")  # linked to loop_presentation_001
    assert event.linked_loop_id != rows["loop"]["id"]

    with pytest.raises(HydrationError) as error:
        build_verify_request(rows, event)

    assert any("not to" in issue for issue in error.value.issues)


def test_event_linked_to_this_loop_is_accepted() -> None:
    """A4 permits linked_loop_id to equal the loop being verified; only A3 forbids it."""
    rows = graph_rows()
    payload = json.loads(unlinked_event().model_dump_json())
    payload["linked_loop_id"] = rows["loop"]["id"]

    request = build_verify_request(rows, build_event(payload))
    validate_verifier_request(request)


def test_edges_leaving_the_loop_are_reported() -> None:
    rows = graph_rows()
    rows["edges"][0]["target_node_id"] = "node_in_another_loop"

    with pytest.raises(HydrationError) as error:
        build_verify_request(rows, unlinked_event())

    assert any("ends outside the loop" in issue for issue in error.value.issues)


def test_all_issues_are_reported_at_once() -> None:
    rows = graph_rows()
    rows["loop"]["root_node_id"] = "not_a_node"
    rows["requirements"][0]["must_all_match"] = False
    rows["edges"][0]["source_node_id"] = "also_not_a_node"

    with pytest.raises(HydrationError) as error:
        build_verify_request(rows, unlinked_event())

    assert len(error.value.issues) >= 3


def test_loop_with_no_nodes_is_rejected() -> None:
    rows = graph_rows()
    rows["nodes"] = []

    with pytest.raises(HydrationError) as error:
        build_verify_request(rows, unlinked_event())

    assert any("no nodes" in issue for issue in error.value.issues)


def test_unknown_candidate_node_is_rejected() -> None:
    with pytest.raises(HydrationError) as error:
        build_verify_request(graph_rows(), unlinked_event(), candidate_node_ids=["ghost"])

    assert any("not in this loop" in issue for issue in error.value.issues)


# --------------------------------------------------------------------------------------
# FixtureLoopGraphSource
# --------------------------------------------------------------------------------------


async def test_graph_source_enforces_tenancy() -> None:
    rows = graph_rows()
    source = FixtureLoopGraphSource([rows])
    loop_id = rows["loop"]["id"]
    owner = rows["loop"]["user_id"]

    assert await source.load_loop_graph(owner, loop_id) is not None
    assert await source.load_loop_graph("user_999", loop_id) is None
    assert await source.load_loop_graph(owner, "no_such_loop") is None
    assert isinstance(source, LoopGraphSource)


# --------------------------------------------------------------------------------------
# EventPipeline
# --------------------------------------------------------------------------------------


class RecordingVerifier:
    def __init__(self, requires_replan: bool = False) -> None:
        self.requests: list = []
        self._requires_replan = requires_replan

    async def verify(self, request) -> VerifyEventResponse:
        self.requests.append(request)
        return VerifyEventResponse(
            decisions=[
                NodeEvidenceDecision(
                    node_id=request.nodes[0].id,
                    relationship="UNRELATED",
                    confidence=0.4,
                    reason="stubbed",
                )
            ],
            requires_replan=self._requires_replan,
        )


def build_pipeline(verifier=None, graphs=None, user_id: str = ACTING_USER_ID) -> EventPipeline:
    router = EventRouter(
        repo=FixtureLoopRepository(load_loop_rows()),
        llm=UnavailableLLM(),
        user_id=user_id,
        now_fn=lambda: NOW,
    )
    return EventPipeline(
        router=router,
        graphs=graphs if graphs is not None else FixtureLoopGraphSource([graph_rows()]),
        verifier=verifier,
        user_id=user_id,
    )


async def test_pipeline_routes_then_verifies_the_matched_loop() -> None:
    verifier = RecordingVerifier()
    plan = await build_pipeline(verifier).process(unlinked_event())

    assert plan.matched_loop_ids == ("loop_refund_001",)
    assert plan.compile_new_loop is False
    assert len(verifier.requests) == 1
    assert verifier.requests[0].loop.id == "loop_refund_001"
    validate_verifier_request(verifier.requests[0])


async def test_pipeline_surfaces_replan_requests() -> None:
    plan = await build_pipeline(RecordingVerifier(requires_replan=True)).process(unlinked_event())
    assert plan.replan_loop_ids == ("loop_refund_001",)


async def test_pipeline_never_compiles_when_a_loop_matched() -> None:
    """A matched event is evidence about an existing goal, never a new obligation. The
    compiler also refuses a linked event outright."""
    plan = await build_pipeline(RecordingVerifier()).process(unlinked_event())
    assert plan.outcomes
    assert plan.compile_new_loop is False


async def test_pipeline_does_not_verify_a_prelinked_event_against_the_wrong_loop() -> None:
    """deadline_reached is linked to loop_presentation_001; only the refund graph is
    loadable, so that loop degrades to an unverified match rather than crashing."""
    verifier = RecordingVerifier()
    plan = await build_pipeline(verifier).process(load_event("deadline_reached"))

    assert plan.matched_loop_ids == ("loop_presentation_001",)
    assert verifier.requests == []
    assert plan.skipped and "not loadable" in plan.skipped[0]


async def test_one_unloadable_loop_does_not_stop_the_others() -> None:
    verifier = RecordingVerifier()
    plan = await build_pipeline(verifier, graphs=FixtureLoopGraphSource([])).process(
        unlinked_event()
    )

    assert plan.matched_loop_ids == ("loop_refund_001",)
    assert plan.outcomes[0].verification is None
    assert plan.outcomes[0].error


async def test_link_loop_id_is_only_set_for_an_unambiguous_match() -> None:
    """events.linked_loop_id holds one id, so an ambiguous match must not pick a winner."""
    plan = await build_pipeline(RecordingVerifier()).process(unlinked_event())
    assert plan.link_loop_id == "loop_refund_001"

    ambiguous = EventPlan(event_id="e", outcomes=plan.outcomes + plan.outcomes)
    assert ambiguous.link_loop_id is None

    assert EventPlan(event_id="e").link_loop_id is None


def test_read_side_cannot_reach_the_write_layer() -> None:
    """Structural, not textual: no module under app/events imports the persistence layer.

    The router and pipeline are read-only by contract. An import of `app.db` would be the
    first step to breaking that, and it is far easier to catch here than in review.
    """
    import ast
    import pathlib

    events_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "events"
    offenders: list[str] = []
    for path in sorted(events_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.db"):
                offenders.append(f"{path.name}: from {node.module}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("app.db")
                )

    assert offenders == [], f"read-only modules import the write layer: {offenders}"


def test_pipeline_exposes_no_write_methods() -> None:
    """EventPipeline returns a plan; applying it is the runtime's job, not its own."""
    public = {name for name in dir(EventPipeline) if not name.startswith("_")}
    assert public == {"process"}
