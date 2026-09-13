"""Local A5 context and operation conventions; shared wire DTOs are unchanged."""

from pydantic import AwareDatetime, Field, model_validator

from app.constants import EdgeType
from app.graph.schemas import (
    Action,
    ContractModel,
    Evidence,
    EvidenceRequirement,
    JsonObject,
    NonEmpty,
    ReplanRequest,
)


class ReplanContext(ContractModel):
    state_revision: NonEmpty
    requirements: list[EvidenceRequirement]
    actions: list[Action]
    evidence: list[Evidence]
    available_apps: list[NonEmpty]
    applied_event_ids: list[NonEmpty] = Field(default_factory=list)


class RepairInput(ContractModel):
    request: ReplanRequest
    context: ReplanContext


class EmptyPayload(ContractModel):
    pass


class QuotedChange(ContractModel):
    source_quote: NonEmpty


class NewNode(ContractModel):
    ref: NonEmpty
    title: NonEmpty
    description: str | None = None
    owner: str | None = None
    deadline: AwareDatetime | None = None
    recovery_strategy: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class NodeUpdate(QuotedChange):
    title: NonEmpty | None = None
    description: str | None = None
    recovery_strategy: str | None = None

    @model_validator(mode="after")
    def has_patch(self):
        if not (self.model_fields_set - {"source_quote"}):
            raise ValueError("UPDATE_NODE needs a changed descriptive field")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")
        return self


class Replacement(QuotedChange):
    replacement_node_id: NonEmpty


class DeadlineUpdate(QuotedChange):
    deadline: AwareDatetime | None


class NewEdge(ContractModel):
    source_node_id: NonEmpty
    target_node_id: NonEmpty
    relationship: EdgeType


class RequirementFields(ContractModel):
    type: NonEmpty
    description: NonEmpty
    source_apps: list[NonEmpty] = Field(min_length=1)
    required_fields: JsonObject = Field(default_factory=dict)
    must_all_match: bool = True


class NewRequirement(RequirementFields):
    ref: NonEmpty
    node_id: NonEmpty
    source_quote: NonEmpty | None = None


class RequirementUpdate(RequirementFields, QuotedChange):
    pass


class ActionReference(ContractModel):
    action_ref: NonEmpty


PAYLOAD_TYPES = {
    "ADD_NODE": NewNode,
    "UPDATE_NODE": NodeUpdate,
    "SUPERSEDE_NODE": Replacement,
    "CANCEL_NODE": QuotedChange,
    "VERIFY_NODE": EmptyPayload,
    "ADD_EDGE": NewEdge,
    "REMOVE_EDGE": EmptyPayload,
    "UPDATE_DEADLINE": DeadlineUpdate,
    "ADD_EVIDENCE_REQUIREMENT": NewRequirement,
    "UPDATE_EVIDENCE_REQUIREMENT": RequirementUpdate,
    "ADD_ACTION": ActionReference,
    "CANCEL_ACTION": EmptyPayload,
}
