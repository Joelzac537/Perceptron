"""Provider-to-domain mapping; trusted identity/time never come from model output."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from app.agents.provider_schemas import (
    ActionDraft,
    CompileDraft,
    JsonAtom,
    JsonEntry,
    ReplanDraft,
    VerifyDraft,
    unique_keys,
)
from app.constants import ActionStatus, EdgeType, LoopStatus, NodeStatus
from app.graph.schemas import (
    Action,
    CompiledGraph,
    CompileGoalRequest,
    Edge,
    EvidenceRequirement,
    GraphOperation,
    JsonObject,
    Loop,
    NodeEvidenceDecision,
    OutcomeNode,
    ReplanResponse,
    VerifyEventResponse,
)


def new_id(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


@dataclass
class MappingContext:
    now: datetime
    timezone: str
    id_factory: Callable[[str], str] = new_id
    _ids: dict[tuple[str, str], str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware datetime")

    def identifier(self, kind: str, ref: str) -> str:
        key = (kind, ref)
        if key not in self._ids:
            identifier = self.id_factory(kind)
            if not identifier.strip() or identifier in self._ids.values():
                raise ValueError("ID factory returned an empty or duplicate ID")
            self._ids[key] = identifier
        return self._ids[key]


def decode_value(value: JsonAtom):
    if value.kind == "null":
        return None
    if value.kind == "array":
        return [decode_value(item) for item in value.array_value]
    if value.kind == "object":
        return decode_map(value.object_value)
    result = getattr(value, f"{value.kind}_value")
    if value.kind == "number" and not math.isfinite(result):
        raise ValueError("JSON numbers must be finite")
    return result


def decode_map(entries: list[JsonEntry]) -> JsonObject:
    unique_keys(entries)
    return {entry.key: decode_value(entry.value) for entry in entries}


def _unique_refs(refs: list[str]) -> None:
    if len(refs) != len(set(refs)):
        raise ValueError("Duplicate provider references")


def map_actions(
    drafts: list[ActionDraft], loop_id: str, node_ids: dict[str, str], context: MappingContext
) -> list[Action]:
    _unique_refs([draft.ref for draft in drafts])
    result = []
    for draft in drafts:
        if draft.node_ref is not None and draft.node_ref not in node_ids:
            raise ValueError("Action references an unknown node")
        action_id = context.identifier("action", draft.ref)
        result.append(
            Action(
                id=action_id,
                loop_id=loop_id,
                node_id=node_ids[draft.node_ref] if draft.node_ref is not None else None,
                app=draft.app,
                action_type=draft.action_type,
                parameters=decode_map(draft.parameters),
                risk_level=draft.risk_level,
                requires_approval=draft.requires_approval,
                status=(
                    ActionStatus.AWAITING_APPROVAL
                    if draft.requires_approval
                    else ActionStatus.PROPOSED
                ),
                idempotency_key=f"{loop_id}:{action_id}",
                verification_method=draft.verification_method,
                created_at=context.now,
                updated_at=context.now,
            )
        )
    return result


def map_compile(
    draft: CompileDraft, request: CompileGoalRequest, context: MappingContext
) -> CompiledGraph:
    _unique_refs([node.ref for node in draft.nodes])
    _unique_refs([req.ref for node in draft.nodes for req in node.evidence_requirements])
    loop_id = context.identifier("loop", "compiled")
    node_ids = {node.ref: context.identifier("node", node.ref) for node in draft.nodes}
    if draft.root_ref not in node_ids:
        raise ValueError("Root references an unknown node")
    actions = map_actions(draft.proposed_actions, loop_id, node_ids, context)
    nodes, edges, requirements = [], [], []
    for node in draft.nodes:
        if any(ref not in node_ids for ref in node.depends_on):
            raise ValueError("Dependency references an unknown node")
        node_id = node_ids[node.ref]
        node_requirements = [
            EvidenceRequirement(
                id=context.identifier("req", req.ref),
                node_id=node_id,
                type=req.type,
                description=req.description,
                source_apps=req.source_apps,
                required_fields=decode_map(req.required_fields),
                must_all_match=req.must_all_match,
                created_at=context.now,
            )
            for req in node.evidence_requirements
        ]
        requirements.extend(node_requirements)
        nodes.append(
            OutcomeNode(
                id=node_id,
                loop_id=loop_id,
                title=node.title,
                description=node.description,
                status=NodeStatus.BLOCKED if node.depends_on else NodeStatus.ACTIVE,
                owner=node.owner,
                deadline=node.deadline,
                depends_on=[node_ids[ref] for ref in node.depends_on],
                evidence_requirement_ids=[req.id for req in node_requirements],
                action_ids=[action.id for action in actions if action.node_id == node_id],
                recovery_strategy=node.recovery_strategy,
                metadata=decode_map(node.metadata),
                created_at=context.now,
                updated_at=context.now,
            )
        )
        for ref in node.depends_on:
            edges.append(
                Edge(
                    id=context.identifier("edge", f"{node.ref}\0{ref}"),
                    loop_id=loop_id,
                    source_node_id=node_id,
                    target_node_id=node_ids[ref],
                    relationship=EdgeType.DEPENDS_ON,
                    reason="Compiler identified this outcome as a required prerequisite.",
                    created_at=context.now,
                )
            )
    return CompiledGraph(
        loop=Loop(
            id=loop_id,
            user_id=request.user_id,
            title=draft.title,
            goal=draft.goal,
            status=LoopStatus.BLOCKED if draft.clarification_needed else LoopStatus.ACTIVE,
            root_node_id=node_ids[draft.root_ref],
            node_ids=list(node_ids.values()),
            source_event_ids=[request.source_event.id] if request.source_event else [],
            created_at=context.now,
            updated_at=context.now,
        ),
        nodes=nodes,
        edges=edges,
        evidence_requirements=requirements,
        proposed_actions=actions,
        assumptions=draft.assumptions,
        clarification_needed=draft.clarification_needed,
        clarification_question=draft.clarification_question,
    )


def map_verify(draft: VerifyDraft, known_node_ids: set[str]) -> VerifyEventResponse:
    _unique_refs([decision.node_id for decision in draft.decisions])
    if any(decision.node_id not in known_node_ids for decision in draft.decisions):
        raise ValueError("Evidence decision references an unknown node")
    return VerifyEventResponse(
        decisions=[
            NodeEvidenceDecision(
                **decision.model_dump(exclude={"extracted_fields"}),
                extracted_fields=decode_map(decision.extracted_fields),
            )
            for decision in draft.decisions
        ],
        requires_replan=draft.requires_replan,
    )


def map_replan(
    draft: ReplanDraft, loop_id: str, node_ids: dict[str, str], context: MappingContext
) -> ReplanResponse:
    """Legacy A2 shape conversion only, not the A5 service's repair pipeline.

    A5 uses graph.repair_validation.map_repair to normalize and simulate full repairs.
    Here node_ids must include validated additions supplied by the caller.
    This function must never be used as authorization to apply operations.
    """
    return ReplanResponse(
        operations=[
            GraphOperation(
                type=operation.type,
                target_id=operation.target_id,
                payload=decode_map(operation.payload),
                reason=operation.reason,
            )
            for operation in draft.operations
        ],
        proposed_actions=map_actions(draft.proposed_actions, loop_id, node_ids, context),
        summary=draft.summary,
    )
