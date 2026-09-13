import copy

import pytest
from conftest import FIXTURES

from app.graph.schemas import CompiledGraph, CompileGoalRequest
from app.graph.semantic_validation import GraphValidationError, validate_compiled_graph


def validate(data):
    return validate_compiled_graph(CompiledGraph.model_validate(data))


@pytest.mark.parametrize("scenario", ["refund", "promise", "renewal"])
def test_expected_graph_is_valid_without_mutation(scenario):
    graph = CompiledGraph.model_validate_json(
        (FIXTURES / f"{scenario}_compiled_graph.json").read_text(encoding="utf-8")
    )
    request = CompileGoalRequest.model_validate_json(
        (FIXTURES / f"{scenario}_goal.json").read_text(encoding="utf-8")
    )
    before = graph.model_dump_json()
    assert validate_compiled_graph(graph, request) is graph
    assert graph.model_dump_json() == before


def test_missing_root_rejected(refund_data):
    refund_data["loop"]["root_node_id"] = "node_missing"
    with pytest.raises(GraphValidationError, match="Root node missing"):
        validate(refund_data)


def test_unknown_dependency_rejected(refund_data):
    refund_data["nodes"][1]["depends_on"] = ["node_missing"]
    with pytest.raises(GraphValidationError, match="Unknown dependency"):
        validate(refund_data)


def test_unknown_edge_endpoint_rejected(refund_data):
    refund_data["edges"][0]["target_node_id"] = "node_missing"
    with pytest.raises(GraphValidationError, match="unknown node"):
        validate(refund_data)


def test_cycle_rejected_even_when_edges_match(refund_data):
    first, last = refund_data["nodes"][0], refund_data["nodes"][-1]
    first["depends_on"] = [last["id"]]
    first["status"] = "BLOCKED"
    edge = copy.deepcopy(refund_data["edges"][0])
    edge.update(id="edge_cycle", source_node_id=first["id"], target_node_id=last["id"])
    refund_data["edges"].append(edge)
    with pytest.raises(GraphValidationError, match="Dependency cycle"):
        validate(refund_data)


def test_self_dependency_rejected(refund_data):
    refund_data["nodes"][0]["depends_on"] = [refund_data["nodes"][0]["id"]]
    with pytest.raises(GraphValidationError, match="Self-dependency"):
        validate(refund_data)


def test_duplicate_semantic_edge_rejected(refund_data):
    edge = copy.deepcopy(refund_data["edges"][0])
    edge["id"] = "edge_duplicate"
    refund_data["edges"].append(edge)
    with pytest.raises(GraphValidationError, match="Duplicate semantic edge"):
        validate(refund_data)


def test_disconnected_root_rejected(refund_data):
    refund_data["loop"]["root_node_id"] = refund_data["nodes"][0]["id"]
    with pytest.raises(GraphValidationError, match="disconnected"):
        validate(refund_data)


@pytest.mark.parametrize("collection", ["nodes", "edges", "proposed_actions"])
def test_cross_loop_reference_rejected(refund_data, collection):
    refund_data[collection][0]["loop_id"] = "loop_other"
    with pytest.raises(GraphValidationError, match="another loop"):
        validate(refund_data)


def test_missing_evidence_contract_rejected(refund_data):
    refund_data["nodes"][0]["evidence_requirement_ids"] = []
    with pytest.raises(GraphValidationError, match="needs an evidence requirement"):
        validate(refund_data)


def test_requirement_cannot_attach_to_wrong_node(refund_data):
    refund_data["evidence_requirements"][0]["node_id"] = refund_data["nodes"][-1]["id"]
    with pytest.raises(GraphValidationError, match="Invalid requirement reference"):
        validate(refund_data)


def test_action_cannot_attach_to_unknown_node(refund_data):
    refund_data["proposed_actions"][0]["node_id"] = "node_missing"
    with pytest.raises(GraphValidationError, match="Invalid node reference"):
        validate(refund_data)


def test_dependency_representations_must_agree(refund_data):
    refund_data["edges"].pop()
    with pytest.raises(GraphValidationError, match="disagree"):
        validate(refund_data)


def test_duplicate_idempotency_key_rejected(refund_data):
    action = copy.deepcopy(refund_data["proposed_actions"][0])
    action.update(id="action_duplicate", node_id=None)
    refund_data["proposed_actions"].append(action)
    with pytest.raises(GraphValidationError, match="Duplicate idempotency"):
        validate(refund_data)


@pytest.mark.parametrize(
    "action_type,app",
    [
        ("SEND_EMAIL", "gmail"),
        ("SEND_SLACK_MESSAGE", "slack"),
        ("UPDATE_CALENDAR_EVENT", "google_calendar"),
        ("CANCEL_CALENDAR_EVENT", "google_calendar"),
    ],
)
def test_model_cannot_downgrade_communication_or_calendar_changes(refund_data, action_type, app):
    refund_data["proposed_actions"][0].update(action_type=action_type, app=app)
    with pytest.raises(GraphValidationError, match="requires approval"):
        validate(refund_data)


def test_approval_gated_message_proposal_is_valid(refund_data):
    refund_data["proposed_actions"][0].update(
        action_type="SEND_EMAIL",
        app="gmail",
        risk_level="MEDIUM",
        requires_approval=True,
        status="AWAITING_APPROVAL",
    )
    validate(refund_data)


def test_high_risk_action_rejected(refund_data):
    refund_data["proposed_actions"][0].update(action_type="MAKE_PAYMENT", risk_level="HIGH")
    with pytest.raises(GraphValidationError, match="outside MVP"):
        validate(refund_data)


@pytest.mark.parametrize("status", ["VERIFIED", "EXECUTED", "APPROVED"])
def test_compiler_cannot_claim_execution_or_approval(refund_data, status):
    refund_data["proposed_actions"][0]["status"] = status
    with pytest.raises(GraphValidationError, match="must be a proposal"):
        validate(refund_data)


def test_compiler_cannot_claim_outcome_verified(refund_data):
    refund_data["nodes"][0]["status"] = "VERIFIED"
    with pytest.raises(GraphValidationError, match="must be unresolved"):
        validate(refund_data)


def test_dependency_cannot_activate_early(refund_data):
    refund_data["nodes"][-1]["status"] = "ACTIVE"
    with pytest.raises(GraphValidationError, match="unresolved prerequisites"):
        validate(refund_data)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("available_apps", [], "unavailable"),
        ("user_id", "user_other", "user identity"),
    ],
)
def test_compiler_request_context_is_enforced(refund_data, field, value, match):
    request = CompileGoalRequest.model_validate_json(
        (FIXTURES / "refund_goal.json").read_text(encoding="utf-8")
    )
    setattr(request, field, value)
    with pytest.raises(GraphValidationError, match=match):
        validate_compiled_graph(CompiledGraph.model_validate(refund_data), request)


def test_clarification_defers_actions(refund_data):
    refund_data.update(clarification_needed=True, clarification_question="Which recipient?")
    with pytest.raises(GraphValidationError, match="Clarification must be resolved"):
        validate(refund_data)


def test_invalid_graph_is_not_mutated(refund_data):
    refund_data["loop"]["root_node_id"] = "missing"
    graph = CompiledGraph.model_validate(refund_data)
    before = graph.model_dump_json()
    with pytest.raises(GraphValidationError):
        validate_compiled_graph(graph)
    assert graph.model_dump_json() == before
