"""Shared wire contracts; bootstrapped from the contract document for Teammate C.

These are domain DTOs, not provider-facing Structured Outputs schemas. Open JSON
maps are intentional here; the live model adapter must use closed provider DTOs.
"""

from typing import Annotated, Self

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


class Event(ContractModel):
    id: NonEmpty
    source_app: NonEmpty
    event_type: EventType
    external_id: str | None = None
    timestamp: AwareDatetime
    actor: str | None = None
    subject: str | None = None
    content: str | None = None
    attachments: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    linked_loop_id: str | None = None
    processed: bool = False


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
