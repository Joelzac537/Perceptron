"""Shared wire contracts; bootstrapped from the contract document for Teammate C.

These are domain DTOs, not provider-facing Structured Outputs schemas. Open JSON
maps are intentional here; the live model adapter must use closed provider DTOs.
"""

from typing import Annotated, Self,Optional
from datetime import datetime
from enum import Enum
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.constants import (
    ActionStatus,
    ApprovalStatus,
    EdgeType,
    EventType,
    EvidenceRelationship,
    GraphOperationType,
    LoopStatus,
    NodeStatus,
    RiskLevel,
)

NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
JsonObject = dict[str, JsonValue]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Loop(ContractModel):
    id: NonEmpty
    user_id: NonEmpty
    title: NonEmpty
    goal: NonEmpty
    status: LoopStatus
    root_node_id: NonEmpty
    source_event_ids: list[NonEmpty] = Field(default_factory=list)
    node_ids: list[NonEmpty] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class OutcomeNode(ContractModel):
    id: NonEmpty
    loop_id: NonEmpty
    title: NonEmpty
    description: str | None = None
    status: NodeStatus
    owner: str | None = None
    deadline: AwareDatetime | None = None
    depends_on: list[NonEmpty] = Field(default_factory=list)
    evidence_requirement_ids: list[NonEmpty] = Field(default_factory=list)
    evidence_ids: list[NonEmpty] = Field(default_factory=list)
    action_ids: list[NonEmpty] = Field(default_factory=list)
    recovery_strategy: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Edge(ContractModel):
    id: NonEmpty
    loop_id: NonEmpty
    source_node_id: NonEmpty
    target_node_id: NonEmpty
    relationship: EdgeType
    reason: str | None = None
    created_at: AwareDatetime


class EvidenceRequirement(ContractModel):
    id: NonEmpty
    node_id: NonEmpty
    type: NonEmpty
    description: NonEmpty
    source_apps: list[NonEmpty] = Field(min_length=1)
    required_fields: JsonObject = Field(default_factory=dict)
    must_all_match: bool = True
    created_at: AwareDatetime


class Evidence(ContractModel):
    id: NonEmpty
    node_id: NonEmpty
    event_id: NonEmpty
    relationship: EvidenceRelationship
    confidence: Confidence
    reason: NonEmpty
    extracted_fields: JsonObject = Field(default_factory=dict)
    verified: bool = False
    created_at: AwareDatetime


class Action(ContractModel):
    id: NonEmpty
    loop_id: NonEmpty
    node_id: NonEmpty | None = None
    app: NonEmpty
    action_type: NonEmpty
    parameters: JsonObject
    risk_level: RiskLevel
    requires_approval: bool
    status: ActionStatus
    idempotency_key: NonEmpty
    external_id: str | None = None
    verification_method: str | None = None
    error: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Approval(ContractModel):
    id: NonEmpty
    action_id: NonEmpty
    loop_id: NonEmpty
    status: ApprovalStatus
    requested_at: AwareDatetime
    resolved_at: AwareDatetime | None = None
    user_comment: str | None = None


class ActivityLog(ContractModel):
    id: NonEmpty
    loop_id: NonEmpty
    activity_type: NonEmpty
    message: NonEmpty
    metadata: JsonObject = Field(default_factory=dict)
    created_at: AwareDatetime


class CompileGoalRequest(ContractModel):
    user_id: NonEmpty
    user_goal: NonEmpty | None = None
    source_event: Event | None = None
    available_apps: list[NonEmpty]

    @model_validator(mode="after")
    def has_goal_context(self) -> Self:
        if self.user_goal is None and self.source_event is None:
            raise ValueError("At least one of user_goal or source_event is required")
        return self


class CompiledGraph(ContractModel):
    loop: Loop
    nodes: list[OutcomeNode]
    edges: list[Edge]
    evidence_requirements: list[EvidenceRequirement]
    proposed_actions: list[Action]
    assumptions: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def clarification_has_question(self) -> Self:
        if self.clarification_needed and not (
            self.clarification_question and self.clarification_question.strip()
        ):
            raise ValueError("clarification_needed requires a clarification_question")
        return self


class RouteEventRequest(ContractModel):
    event: Event


class LoopMatch(ContractModel):
    loop_id: NonEmpty
    confidence: Confidence
    reason: NonEmpty


class RouteEventResponse(ContractModel):
    matches: list[LoopMatch]
    create_new_loop_candidate: bool = False


class VerifyEventRequest(ContractModel):
    loop: Loop
    nodes: list[OutcomeNode]
    requirements: list[EvidenceRequirement]
    event: Event


class NodeEvidenceDecision(ContractModel):
    node_id: NonEmpty
    relationship: EvidenceRelationship
    confidence: Confidence
    reason: NonEmpty
    extracted_fields: JsonObject = Field(default_factory=dict)
    evidence_satisfies_requirement: bool = False

    @model_validator(mode="after")
    def satisfaction_requires_proof(self) -> Self:
        if self.evidence_satisfies_requirement and self.relationship != EvidenceRelationship.PROVES:
            raise ValueError("Only PROVES can satisfy an evidence requirement")
        return self


class VerifyEventResponse(ContractModel):
    decisions: list[NodeEvidenceDecision]
    requires_replan: bool = False


class ReplanRequest(ContractModel):
    loop: Loop
    nodes: list[OutcomeNode]
    edges: list[Edge]
    triggering_event: Event
    evidence_decisions: list[NodeEvidenceDecision]


class GraphOperation(ContractModel):
    type: GraphOperationType
    target_id: NonEmpty | None = None
    payload: JsonObject = Field(default_factory=dict)
    reason: NonEmpty


class ReplanResponse(ContractModel):
    operations: list[GraphOperation]
    proposed_actions: list[Action]
    summary: NonEmpty


class ExecuteActionRequest(ContractModel):
    action: Action


class ExecuteActionResponse(ContractModel):
    action_id: NonEmpty
    success: bool
    external_id: str | None = None
    raw_result: JsonObject = Field(default_factory=dict)
    error: str | None = None


class VerifyActionRequest(ContractModel):
    action: Action
    execution_result: ExecuteActionResponse


class VerifyActionResponse(ContractModel):
    action_id: NonEmpty
    verified: bool
    observed_state: JsonObject = Field(default_factory=dict)
    reason: NonEmpty

class EventType(str, Enum):
    """Every external change enters the pipeline as one of these."""

    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    MESSAGE_SENT = "MESSAGE_SENT"
    # Added alongside DOCUMENT_DELETED below. Doc 03 §4.4 covers a message
    # arriving but not one being changed afterwards, and a loop can hinge on
    # exactly that -- "they edited the deadline out of their reply".
    MESSAGE_UPDATED = "MESSAGE_UPDATED"
    MESSAGE_DELETED = "MESSAGE_DELETED"
    DOCUMENT_CREATED = "DOCUMENT_CREATED"
    DOCUMENT_UPDATED = "DOCUMENT_UPDATED"
    # Added to the doc 03 §4.4 list, which has CALENDAR_EVENT_DELETED but no
    # document equivalent. Drive reports deletions explicitly and a loop can
    # hinge on one ("the signed contract is gone"), so it needs a type.
    DOCUMENT_DELETED = "DOCUMENT_DELETED"
    DOCUMENT_FOUND = "DOCUMENT_FOUND"
    CALENDAR_EVENT_CREATED = "CALENDAR_EVENT_CREATED"
    CALENDAR_EVENT_UPDATED = "CALENDAR_EVENT_UPDATED"
    CALENDAR_EVENT_DELETED = "CALENDAR_EVENT_DELETED"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    USER_APPROVED = "USER_APPROVED"
    USER_REJECTED = "USER_REJECTED"
    USER_INPUT = "USER_INPUT"
    SYSTEM_EVENT = "SYSTEM_EVENT"


class Event(ContractModel):
    """A normalized external event.

    Reasoning code must never depend on Gmail-specific or Slack-specific
    payload structure -- that is the whole point of this type existing.
    """

    id: str
    source_app: str
    event_type: EventType

    external_id: Optional[str] = None

    # The app's own timestamp where available, not when we noticed it.
    # Deadlines depend on when the merchant sent the mail, not when we polled.
    timestamp: datetime

    actor: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None

    attachments: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    linked_loop_id: Optional[str] = None
    processed: bool = False

    @property
    def dedup_key(self) -> str:
        """Identity for deduplication: source_app + external_id + version.

        Webhooks can be delivered more than once and the pollers see the same
        item every pass. One real-world event, one Event.

        The version matters: without it, editing or deleting a calendar event
        produces the same key as its creation and gets silently dropped as a
        duplicate. metadata["version"] holds the item's own change stamp
        (Google's `updated`, Slack's `edited.ts`), so a change to an item we
        have already seen counts as a new event.
        """
        version = self.metadata.get("version") or ""
        return f"{self.source_app}:{self.external_id or self.id}:{version}"
