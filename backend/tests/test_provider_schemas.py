from datetime import UTC, datetime

import pytest
from openai.lib._parsing._responses import type_to_text_format_param
from provider_helpers import encode_map, encode_value
from pydantic import ValidationError

from app.agents.mapping import MappingContext, decode_map, decode_value, map_replan, map_verify
from app.agents.provider_schemas import (
    CompileDraft,
    JsonAtom,
    ReplanDraft,
    VerifyDraft,
    assert_closed_schema,
)
from app.graph.schemas import CompiledGraph


@pytest.mark.parametrize("model", [CompileDraft, VerifyDraft, ReplanDraft])
def test_provider_schemas_are_closed_and_sdk_accepts_them(model):
    assert_closed_schema(model)
    sdk_format = type_to_text_format_param(model)
    assert sdk_format["strict"] is True
    assert sdk_format["type"] == "json_schema"


def test_raw_domain_schema_is_not_a_provider_schema():
    with pytest.raises(ValueError):
        assert_closed_schema(CompiledGraph)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "a string",
        129.0,
        9007199254740993,
        [],
        {},
        {"nested": [{"amount": 129, "currency": "USD"}, False, None]},
    ],
)
def test_recursive_json_roundtrip_preserves_types(value):
    encoded = encode_value(value)
    wire = JsonAtom.model_validate_json(encoded.model_dump_json())
    assert decode_value(wire) == value
    assert type(decode_value(wire)) is type(value)


def test_duplicate_map_keys_are_rejected():
    entries = encode_map({"order": "A1298"}) * 2
    with pytest.raises(ValueError, match="Duplicate JSON"):
        decode_map(entries)


def test_inconsistent_tag_rejected():
    data = encode_value("text").model_dump()
    data["boolean_value"] = True
    with pytest.raises(ValidationError, match="selected kind"):
        JsonAtom.model_validate(data)


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_nonfinite_numbers_rejected(value):
    with pytest.raises(ValidationError):
        encode_value(value)


def test_verify_mapping_enforces_references_and_preserves_fields():
    draft = VerifyDraft(
        decisions=[
            dict(
                node_id="node_refund",
                relationship="PROVES",
                confidence=0.98,
                reason="Merchant confirms refund",
                extracted_fields=encode_map({"amount": 129.0}),
                evidence_satisfies_requirement=True,
            )
        ],
        requires_replan=False,
    )
    result = map_verify(draft, {"node_refund"})
    assert result.decisions[0].extracted_fields == {"amount": 129.0}
    with pytest.raises(ValueError, match="unknown node"):
        map_verify(draft, {"node_other"})
    draft.decisions *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        map_verify(draft, {"node_refund"})


def test_replan_shape_mapping_preserves_existing_ids_and_operation_payload():
    draft = ReplanDraft(
        operations=[
            dict(
                type="UPDATE_DEADLINE",
                target_id="node_existing",
                payload=encode_map({"deadline": "2026-09-28T17:00:00-04:00"}),
                reason="Merchant announced delay",
            )
        ],
        proposed_actions=[],
        summary="Move deadline",
    )
    context = MappingContext(datetime(2026, 9, 13, tzinfo=UTC), "UTC")
    response = map_replan(draft, "loop_existing", {"node_existing": "node_existing"}, context)
    assert response.operations[0].target_id == "node_existing"
    assert response.operations[0].payload == {"deadline": "2026-09-28T17:00:00-04:00"}


def test_id_factory_cannot_generate_collisions():
    context = MappingContext(datetime(2026, 9, 13, tzinfo=UTC), "UTC", lambda _: "same")
    assert context.identifier("node", "first") == "same"
    assert context.identifier("node", "first") == "same"
    with pytest.raises(ValueError, match="duplicate ID"):
        context.identifier("node", "second")
