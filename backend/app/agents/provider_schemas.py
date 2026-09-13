"""Closed provider DTOs. All fields are required; nullable fields must be explicit.

JSON maps are represented as key/value entries. Recursive tagged values preserve
nested arrays/objects without open additionalProperties or JSON encoded as prose.
Graph-operation payload semantics are validated by the A5 repair boundary.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.constants import EvidenceRelationship, GraphOperationType, RiskLevel
from app.graph.schemas import Confidence, NonEmpty


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always", allow_inf_nan=False)


class JsonEntry(ProviderModel):
    key: str
    value: "JsonAtom"


class JsonAtom(ProviderModel):
    kind: Literal["string", "number", "boolean", "null", "array", "object"]
    string_value: str | None
    number_value: int | float | None
    boolean_value: bool | None
    array_value: list["JsonAtom"] | None
    object_value: list[JsonEntry] | None

    @model_validator(mode="after")
    def only_selected_value(self) -> Self:
        for kind in ("string", "number", "boolean", "array", "object"):
            value = getattr(self, f"{kind}_value")
            if (value is not None) != (self.kind == kind):
                raise ValueError("Exactly the selected kind's value must be non-null")
        if self.object_value is not None:
            unique_keys(self.object_value)
        return self


def unique_keys(entries: list[JsonEntry]) -> None:
    keys = [entry.key for entry in entries]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate JSON object keys")


class RequirementDraft(ProviderModel):
    ref: NonEmpty
    type: NonEmpty
    description: NonEmpty
    source_apps: list[NonEmpty]
    required_fields: list[JsonEntry]
    must_all_match: bool


class NodeDraft(ProviderModel):
    ref: NonEmpty
    title: NonEmpty
    description: str | None
    owner: str | None
    deadline: str | None
    depends_on: list[NonEmpty]
    evidence_requirements: list[RequirementDraft]
    recovery_strategy: str | None
    metadata: list[JsonEntry]


class ActionDraft(ProviderModel):
    ref: NonEmpty
    node_ref: NonEmpty | None
    app: NonEmpty
    action_type: NonEmpty
    parameters: list[JsonEntry]
    risk_level: RiskLevel
    requires_approval: bool
    verification_method: str | None


class CompileDraft(ProviderModel):
    title: NonEmpty
    goal: NonEmpty
    root_ref: NonEmpty
    nodes: list[NodeDraft]
    proposed_actions: list[ActionDraft]
    assumptions: list[str]
    clarification_needed: bool
    clarification_question: str | None


class DecisionDraft(ProviderModel):
    node_id: NonEmpty
    relationship: EvidenceRelationship
    confidence: Confidence
    reason: NonEmpty
    extracted_fields: list[JsonEntry]
    evidence_satisfies_requirement: bool


class VerifyDraft(ProviderModel):
    decisions: list[DecisionDraft]
    requires_replan: bool


class OperationDraft(ProviderModel):
    type: GraphOperationType
    target_id: NonEmpty | None
    payload: list[JsonEntry]
    reason: NonEmpty


class ReplanDraft(ProviderModel):
    operations: list[OperationDraft]
    proposed_actions: list[ActionDraft]
    summary: NonEmpty


def assert_closed_schema(model: type[ProviderModel]) -> None:
    """Reject accidental domain DTOs/open maps before making a model request.

    This checks the subset emitted by these DTOs; it is not a general API validator.
    Recursive references are checked through their definitions without dereferencing.
    """

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if "default" in value:
                raise ValueError("Provider schemas cannot have defaults")
            if value.get("type") == "object":
                if value.get("additionalProperties") is not False:
                    raise ValueError("Provider schemas must have closed objects")
                if set(value.get("required", [])) != set(value.get("properties", {})):
                    raise ValueError("All provider properties must be required")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    schema = model.model_json_schema()
    if schema.get("type") != "object":
        raise ValueError("Provider output must be a root object")
    visit(schema)
