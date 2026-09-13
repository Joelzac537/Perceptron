import json

import pytest
from conftest import FIXTURES
from pydantic import ValidationError

from app.graph.schemas import (
    CompiledGraph,
    CompileGoalRequest,
    Event,
    Evidence,
    GraphOperation,
    NodeEvidenceDecision,
)


@pytest.mark.parametrize("scenario", ["refund", "promise", "renewal"])
def test_fixture_round_trip(scenario):
    for suffix, model in (("goal", CompileGoalRequest), ("compiled_graph", CompiledGraph)):
        instance = model.model_validate_json(
            (FIXTURES / f"{scenario}_{suffix}.json").read_text(encoding="utf-8")
        )
        assert model.model_validate_json(instance.model_dump_json()) == instance


@pytest.mark.parametrize("goal", [None, "", "   "])
def test_goal_intake_rejects_missing_or_blank_context(goal):
    with pytest.raises(ValidationError):
        CompileGoalRequest(user_id="user_001", user_goal=goal, available_apps=[])


def test_source_event_alone_is_valid_context():
    data = json.loads((FIXTURES / "refund_goal.json").read_text(encoding="utf-8"))
    data.pop("user_goal")
    assert CompileGoalRequest.model_validate(data).source_event is not None


def test_reject_unknown_fields(refund_data):
    refund_data["loop"]["invented_status"] = "done"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CompiledGraph.model_validate(refund_data)


@pytest.mark.parametrize("timestamp", ["2026-09-13T09:35:00", "invalid", "2026-09-13"])
def test_timestamps_require_timezone(refund_data, timestamp):
    refund_data["nodes"][0]["deadline"] = timestamp
    with pytest.raises(ValidationError):
        CompiledGraph.model_validate(refund_data)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
def test_confidence_is_bounded(confidence):
    with pytest.raises(ValidationError):
        NodeEvidenceDecision(
            node_id="node_1", relationship="PROVES", confidence=confidence, reason="Test"
        )


@pytest.mark.parametrize("relationship", ["INSUFFICIENT", "UNRELATED", "PARTIALLY_SUPPORTS"])
def test_nonproof_cannot_satisfy_requirement(relationship):
    with pytest.raises(ValidationError, match="Only PROVES"):
        NodeEvidenceDecision(
            node_id="node_1",
            relationship=relationship,
            confidence=0.99,
            reason="Not enough evidence",
            evidence_satisfies_requirement=True,
        )


def test_assessed_insufficient_evidence_is_not_proof():
    evidence = Evidence(
        id="evidence_1",
        node_id="node_1",
        event_id="event_1",
        relationship="INSUFFICIENT",
        confidence=0.93,
        reason="Draft, not final",
        verified=True,
        created_at="2026-09-18T15:15:00-04:00",
    )
    assert evidence.verified
    assert evidence.relationship == "INSUFFICIENT"


def test_defaults_are_independent():
    data = dict(
        id="event_1",
        source_app="gmail",
        event_type="MESSAGE_RECEIVED",
        timestamp="2026-09-13T09:35:00-04:00",
    )
    first, second = Event(**data), Event(**data)
    first.metadata["thread_id"] = "thread_1"
    first.attachments.append({"filename": "label.pdf"})
    assert second.metadata == {}
    assert second.attachments == []


@pytest.mark.parametrize("value", ["proves", "COMPLETE", "TRIGGERS"])
def test_evidence_relationship_uses_canonical_enum(value):
    with pytest.raises(ValidationError):
        NodeEvidenceDecision(node_id="n", relationship=value, confidence=0.9, reason="Test")


def test_unknown_graph_operation_rejected():
    with pytest.raises(ValidationError):
        GraphOperation(type="REPLACE_ENTIRE_GRAPH", reason="Wrong interface")


def test_clarification_requires_question(refund_data):
    refund_data["clarification_needed"] = True
    with pytest.raises(ValidationError, match="clarification_question"):
        CompiledGraph.model_validate(refund_data)
