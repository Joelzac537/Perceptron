import pytest

from app.agents import smoke
from app.graph.schemas import NodeEvidenceDecision, VerifyEventResponse


def test_cli_requires_explicit_live_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["smoke"])
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2


def test_smoke_validates_a_negative_evidence_result():
    response = VerifyEventResponse(
        decisions=[
            NodeEvidenceDecision(
                node_id="node_smoke",
                relationship="INSUFFICIENT",
                confidence=1,
                reason="No document available",
                evidence_satisfies_requirement=False,
            )
        ]
    )
    smoke.validate_smoke(response)
    response.decisions[0].relationship = "PROVES"
    with pytest.raises(ValueError, match="INSUFFICIENT"):
        smoke.validate_smoke(response)
