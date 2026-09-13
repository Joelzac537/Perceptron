"""Normalize and simulate a whole repair on a copy. No persistence or app writes."""

import hashlib
import json
from datetime import UTC, datetime
from graphlib import CycleError, TopologicalSorter

from app.agents.mapping import MappingContext, decode_map
from app.agents.provider_schemas import ReplanDraft
from app.constants import ActionStatus, EdgeType, NodeStatus
from app.graph.repair_schemas import PAYLOAD_TYPES, RepairInput
from app.graph.schemas import (
    Action,
    Edge,
    EvidenceRequirement,
    GraphOperation,
    OutcomeNode,
    ReplanResponse,
)

INACTIVE = {NodeStatus.SUPERSEDED, NodeStatus.CANCELLED}
IMMUTABLE = INACTIVE | {NodeStatus.VERIFIED}
CANCELLABLE = {
    ActionStatus.PROPOSED,
    ActionStatus.AWAITING_APPROVAL,
    ActionStatus.APPROVED,
    ActionStatus.FAILED,
}


class ReplanInputError(ValueError):
    code = "VALIDATION_ERROR"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_id(data: RepairInput, kind: str, identity) -> str:
    digest = hashlib.sha256(
        canonical(
            [
                data.request.loop.id,
                data.request.triggering_event.id,
                kind,
                identity,
            ]
        ).encode()
    ).hexdigest()[:32]
    return f"{kind}_{digest}"


def node_key(node) -> tuple[str, str]:
    return " ".join(node.title.casefold().split()), " ".join((node.owner or "").casefold().split())


def action_key(action) -> str:
    # Rewording a send within one event must not produce another executable identity.
    parameters = action.parameters
    if action.action_type == "SEND_EMAIL":
        parameters = {"to": parameters.get("to")}
    elif action.action_type == "SEND_SLACK_MESSAGE":
        parameters = {k: parameters.get(k) for k in ("channel_id", "thread_ts")}
    elif action.action_type in {"UPDATE_CALENDAR_EVENT", "CANCEL_CALENDAR_EVENT"}:
        parameters = {"event_id": parameters.get("event_id")}
    elif action.action_type == "CREATE_CALENDAR_EVENT":
        parameters = {"start": parameters.get("start")}
    return canonical([action.node_id, action.app, action.action_type, parameters])


def _validate_state(data: RepairInput) -> None:
    request, context = data.request, data.context
    groups = [request.nodes, request.edges, context.requirements, context.actions, context.evidence]
    identifiers = [item.id for group in groups for item in group]
    require(len(identifiers) == len(set(identifiers)), "Duplicate graph object IDs")
    nodes = {n.id: n for n in request.nodes}
    require(request.loop.root_node_id in nodes, "Missing root node")
    require(
        len(request.loop.node_ids) == len(set(request.loop.node_ids))
        and set(request.loop.node_ids) == set(nodes),
        "Supply all loop nodes",
    )
    require(
        len({a.idempotency_key for a in context.actions}) == len(context.actions),
        "Duplicate action idempotency keys",
    )
    dependencies = {}
    for node in nodes.values():
        require(node.loop_id == request.loop.id, "Node belongs to another loop")
        require(node.updated_at >= node.created_at, "Reversed node timestamps")
        for values in [
            node.depends_on,
            node.action_ids,
            node.evidence_ids,
            node.evidence_requirement_ids,
        ]:
            require(len(values) == len(set(values)), "Duplicate node references")
        require(
            set(node.depends_on) <= nodes.keys() and node.id not in node.depends_on,
            "Invalid dependency reference",
        )
        for objects, ids in [
            (context.requirements, node.evidence_requirement_ids),
            (context.actions, node.action_ids),
            (context.evidence, node.evidence_ids),
        ]:
            require(
                set(ids) == {o.id for o in objects if o.node_id == node.id},
                "Full context must match every node's requirements/actions/evidence",
            )
        if node.status not in INACTIVE:
            require(bool(node.evidence_requirement_ids), "Live node needs evidence requirements")
            require(
                all(nodes[d].status not in INACTIVE for d in node.depends_on),
                "Live node depends on a cancelled or superseded node",
            )
        dependencies[node.id] = set(node.depends_on)
    for objects in [context.requirements, context.evidence]:
        require(all(o.node_id in nodes for o in objects), "Orphan context object")
    for action in context.actions:
        require(action.loop_id == request.loop.id, "Action belongs to another loop")
        require(action.node_id is None or action.node_id in nodes, "Action references unknown node")
    semantic_edges = set()
    actual = set()
    for edge in request.edges:
        require(
            edge.loop_id == request.loop.id
            and edge.source_node_id in nodes
            and edge.target_node_id in nodes,
            "Invalid edge ownership/reference",
        )
        key = (edge.source_node_id, edge.target_node_id, edge.relationship)
        require(key not in semantic_edges, "Duplicate semantic edge")
        semantic_edges.add(key)
        if edge.relationship == EdgeType.DEPENDS_ON:
            actual.add(key[:2])
    require(
        actual == {(n.id, d) for n in nodes.values() for d in n.depends_on},
        "Dependency arrays and edges disagree",
    )
    try:
        tuple(TopologicalSorter(dependencies).static_order())
    except CycleError:
        raise ValueError("Repair creates a dependency cycle") from None
    reachable, pending = set(), [request.loop.root_node_id]
    while pending:
        node_id = pending.pop()
        if node_id not in reachable:
            reachable.add(node_id)
            pending.extend(dependencies[node_id])
    require(
        all(n.id in reachable for n in nodes.values() if n.status not in INACTIVE),
        "Live nodes must remain reachable from the root",
    )


def validate_repair_input(data: RepairInput) -> None:
    try:
        _validate_state(data)
        request = data.request
        require(
            request.triggering_event.linked_loop_id in (None, request.loop.id),
            "Trigger belongs to another loop",
        )
        ids = [d.node_id for d in request.evidence_decisions]
        require(
            len(ids) == len(set(ids)) and set(ids) <= set(request.loop.node_ids),
            "Invalid evidence decision references",
        )
    except ValueError as exc:
        raise ReplanInputError(str(exc)) from None


def _quote(data: RepairInput, quote: str) -> None:
    event = data.request.triggering_event
    texts = [event.actor, event.subject, event.content]
    texts.extend(a.get("extracted_text") for a in event.attachments)
    require(
        any(isinstance(text, str) and quote in text for text in texts),
        "Change source_quote must occur verbatim in the triggering event",
    )


def _changed_requirement(data: RepairInput, node_id: str, quote: str) -> None:
    _quote(data, quote)
    require(
        data.request.triggering_event.event_type == "USER_INPUT"
        or any(
            d.node_id == node_id and d.relationship in {"SUPERSEDES", "CONTRADICTS"}
            for d in data.request.evidence_decisions
        ),
        "Requirement change needs user input or assessed changed evidence for the node",
    )


def map_repair(draft: ReplanDraft, data: RepairInput, mapping: MappingContext) -> ReplanResponse:
    parsed = [
        (op, PAYLOAD_TYPES[op.type].model_validate(decode_map(op.payload)))
        for op in draft.operations
    ]
    references = {n.id: n.id for n in data.request.nodes}
    existing_ids = {
        o.id
        for group in [
            data.request.nodes,
            data.request.edges,
            data.context.requirements,
            data.context.actions,
            data.context.evidence,
        ]
        for o in group
    }
    replacements = {}
    for op, payload in parsed:
        if op.type == "SUPERSEDE_NODE":
            require(
                payload.replacement_node_id not in replacements, "Replacement used more than once"
            )
            replacements[payload.replacement_node_id] = op.target_id
    for op, payload in parsed:
        if op.type == "ADD_NODE":
            require(op.target_id is None, "ADD_NODE target_id must be null")
            require(payload.ref not in references, "Duplicate new node reference")
            identity = (
                ["replacement", replacements[payload.ref]]
                if payload.ref in replacements
                else ["outcome", *node_key(payload)]
            )
            references[payload.ref] = stable_id(data, "node", identity)
    require(
        len(references.values()) == len(set(references.values())), "Duplicate semantic new node"
    )

    def node_id(ref):
        require(ref in references, "Unknown node reference")
        return references[ref]

    actions, action_refs = [], {}
    for source in draft.proposed_actions:
        require(source.ref not in action_refs, "Duplicate action reference")
        action = Action(
            id="temporary",
            loop_id=data.request.loop.id,
            node_id=node_id(source.node_ref),
            app=source.app,
            action_type=source.action_type,
            parameters=decode_map(source.parameters),
            risk_level=source.risk_level,
            requires_approval=source.requires_approval,
            status=ActionStatus.AWAITING_APPROVAL
            if source.requires_approval
            else ActionStatus.PROPOSED,
            idempotency_key="temporary",
            verification_method=source.verification_method,
            created_at=mapping.now,
            updated_at=mapping.now,
        )
        action.id = stable_id(data, "action", action_key(action))
        action.idempotency_key = f"{data.request.loop.id}:{action.id}"
        action_refs[source.ref] = action.id
        actions.append(action)
    operations, requirement_refs = [], set()
    for op, payload in parsed:
        kind, target = op.type, op.target_id
        if kind in {"ADD_NODE", "ADD_EDGE", "ADD_EVIDENCE_REQUIREMENT", "ADD_ACTION"}:
            require(target is None, "Addition target_id must be null")
        else:
            require(target is not None, "Mutation target_id is required")
        if kind == "ADD_NODE":
            body = OutcomeNode(
                id=node_id(payload.ref),
                loop_id=data.request.loop.id,
                title=payload.title,
                description=payload.description,
                owner=payload.owner,
                deadline=payload.deadline,
                status=NodeStatus.ACTIVE,
                recovery_strategy=payload.recovery_strategy,
                metadata=payload.metadata,
                created_at=mapping.now,
                updated_at=mapping.now,
            ).model_dump(mode="json")
        elif kind == "ADD_EDGE":
            source, destination = node_id(payload.source_node_id), node_id(payload.target_node_id)
            body = Edge(
                id=stable_id(data, "edge", [source, destination, payload.relationship]),
                loop_id=data.request.loop.id,
                source_node_id=source,
                target_node_id=destination,
                relationship=payload.relationship,
                reason=op.reason,
                created_at=mapping.now,
            ).model_dump(mode="json")
        elif kind == "ADD_EVIDENCE_REQUIREMENT":
            require(payload.ref not in requirement_refs, "Duplicate requirement reference")
            requirement_refs.add(payload.ref)
            owner = node_id(payload.node_id)
            fields = payload.model_dump(exclude={"ref", "node_id", "source_quote"})
            body = EvidenceRequirement(
                id=stable_id(data, "req", [owner, fields]),
                node_id=owner,
                created_at=mapping.now,
                **fields,
            ).model_dump(mode="json")
            if payload.source_quote is not None:
                body["source_quote"] = payload.source_quote
        elif kind == "ADD_ACTION":
            require(payload.action_ref in action_refs, "ADD_ACTION references unknown proposal")
            body = {"action_id": action_refs[payload.action_ref]}
        else:
            body = payload.model_dump(mode="json", exclude_unset=True)
            if kind in {
                "UPDATE_NODE",
                "SUPERSEDE_NODE",
                "CANCEL_NODE",
                "VERIFY_NODE",
                "UPDATE_DEADLINE",
            }:
                target = node_id(target)
            if kind == "SUPERSEDE_NODE":
                body["replacement_node_id"] = node_id(payload.replacement_node_id)
        if kind in {"ADD_NODE", "ADD_EDGE", "ADD_EVIDENCE_REQUIREMENT"}:
            require(body["id"] not in existing_ids, "Addition duplicates a persisted object")
        operations.append(
            GraphOperation(type=kind, target_id=target, payload=body, reason=op.reason)
        )
    response = ReplanResponse(
        operations=operations, proposed_actions=actions, summary=draft.summary
    )
    preview_repair(data, response, as_of=mapping.now)
    return response


def preview_repair(
    data: RepairInput, response: ReplanResponse, *, as_of: datetime | None = None
) -> RepairInput:
    """Return a validated in-memory candidate. Caller still owns revision check and commit."""
    from app.graph.repair_actions import validate_repair_actions

    data = RepairInput.model_validate_json(data.model_dump_json())
    mutation_time = as_of or datetime.now(UTC)
    require(
        mutation_time.tzinfo is not None and mutation_time.utcoffset() is not None,
        "Preview time must be timezone-aware",
    )
    response = ReplanResponse.model_validate_json(response.model_dump_json())
    validate_repair_input(data)
    candidate = data.model_copy(deep=True)
    request, context = candidate.request, candidate.context
    nodes = {n.id: n for n in request.nodes}
    requirements = {r.id: r for r in context.requirements}
    actions = {a.id: a for a in context.actions}
    edges = {e.id: e for e in request.edges}
    original_nodes = {n.id: n for n in data.request.nodes}
    all_ids = (
        set(nodes)
        | set(requirements)
        | set(actions)
        | set(edges)
        | {e.id for e in context.evidence}
    )
    seen, changed_nodes, replacements, action_markers = set(), set(), {}, []
    terminal_loop = request.loop.status in {"COMPLETED", "CANCELLED", "FAILED"}
    for op in response.operations:
        key = (op.type, op.target_id, canonical(op.payload) if op.target_id is None else "mutation")
        require(key not in seen, "Duplicate repair operation")
        seen.add(key)
        kind, target, body = op.type, op.target_id, op.payload
        if terminal_loop:
            require(kind in {"CANCEL_ACTION", "ADD_ACTION"}, "Closed loop permits cleanup only")
        if kind == "VERIFY_NODE":
            raise ValueError(
                "VERIFY_NODE is runtime-owned; use assessed evidence and completion gates"
            )
        if kind in {"ADD_NODE", "ADD_EDGE", "ADD_EVIDENCE_REQUIREMENT"}:
            require(target is None, "Addition target must be null")
            model = {
                "ADD_NODE": OutcomeNode,
                "ADD_EDGE": Edge,
                "ADD_EVIDENCE_REQUIREMENT": EvidenceRequirement,
            }[kind]
            item = model.model_validate(
                {k: v for k, v in body.items() if k != "source_quote"}
                if kind == "ADD_EVIDENCE_REQUIREMENT"
                else body
            )
            require(item.id not in all_ids, "Duplicate added object ID")
            all_ids.add(item.id)
            if kind == "ADD_NODE":
                require(
                    item.loop_id == request.loop.id and item.status in {"ACTIVE", "BLOCKED"},
                    "New node must belong to loop and remain unresolved",
                )
                require(
                    not item.depends_on
                    and not item.action_ids
                    and not item.evidence_ids
                    and not item.evidence_requirement_ids,
                    "Add references through explicit operations",
                )
                require(
                    all(
                        node_key(item) != node_key(n)
                        for n in nodes.values()
                        if n.status not in INACTIVE
                    ),
                    "Duplicate semantic outcome node",
                )
                nodes[item.id] = item
                request.nodes.append(item)
                request.loop.node_ids.append(item.id)
            elif kind == "ADD_EDGE":
                require(
                    item.source_node_id in nodes and item.target_node_id in nodes,
                    "Add edge references unknown node; add nodes first",
                )
                require(
                    item.relationship in {EdgeType.DEPENDS_ON, EdgeType.SUPERSEDES},
                    "Repair cannot invent evidence or completion edges",
                )
                require(
                    item.source_node_id not in original_nodes
                    or original_nodes[item.source_node_id].status not in IMMUTABLE,
                    "Cannot edit dependencies/history of an immutable node",
                )
                edges[item.id] = item
                request.edges.append(item)
                if item.relationship == EdgeType.DEPENDS_ON:
                    nodes[item.source_node_id].depends_on.append(item.target_node_id)
                    if item.source_node_id in original_nodes:
                        changed_nodes.add(item.source_node_id)
            else:
                require(item.node_id in nodes, "Requirement references unknown node")
                if item.node_id in original_nodes:
                    require(
                        nodes[item.node_id].status not in IMMUTABLE,
                        "Cannot append requirements to historical nodes",
                    )
                    quote = (
                        PAYLOAD_TYPES["CANCEL_NODE"]
                        .model_validate({"source_quote": body.get("source_quote")})
                        .source_quote
                    )
                    _changed_requirement(data, item.node_id, quote)
                    changed_nodes.add(item.node_id)
                require(item.must_all_match, "Optional matching policy is unresolved")
                require(
                    set(item.source_apps) <= set(context.available_apps) | {"loopgraph"},
                    "Requirement uses unavailable app",
                )
                requirements[item.id] = item
                context.requirements.append(item)
                nodes[item.node_id].evidence_requirement_ids.append(item.id)
        elif kind == "ADD_ACTION":
            require(target is None and set(body) == {"action_id"}, "Invalid ADD_ACTION marker")
            action_markers.append(body["action_id"])
        elif kind == "CANCEL_ACTION":
            require(not body and target in actions, "Invalid cancellation target/payload")
            action = actions[target]
            require(
                action.status in CANCELLABLE and action.external_id is None,
                "Only unexecuted internal actions can be cancelled; execution history is immutable",
            )
            action.status = ActionStatus.CANCELLED
        elif kind == "REMOVE_EDGE":
            require(not body and target in edges, "Unknown edge removal or nonempty payload")
            edge = edges.pop(target)
            require(
                edge.relationship == EdgeType.DEPENDS_ON,
                "Repair cannot remove semantic evidence/history edges",
            )
            require(
                edge.source_node_id not in original_nodes
                or original_nodes[edge.source_node_id].status not in IMMUTABLE,
                "Cannot remove dependencies/history from an immutable node",
            )
            request.edges.remove(edge)
            if edge.relationship == EdgeType.DEPENDS_ON:
                nodes[edge.source_node_id].depends_on.remove(edge.target_node_id)
                changed_nodes.add(edge.source_node_id)
        elif kind == "UPDATE_EVIDENCE_REQUIREMENT":
            payload = PAYLOAD_TYPES[kind].model_validate(body)
            require(target in requirements, "Unknown requirement update")
            old = requirements[target]
            require(
                nodes[old.node_id].status not in IMMUTABLE, "Cannot rewrite historical requirements"
            )
            _changed_requirement(data, old.node_id, payload.source_quote)
            require(
                payload.must_all_match
                and set(payload.source_apps) <= set(context.available_apps) | {"loopgraph"},
                "Requirement update weakens matching or uses unavailable sources",
            )
            for name, value in payload.model_dump(exclude={"source_quote"}).items():
                setattr(old, name, value)
            changed_nodes.add(old.node_id)
        else:
            require(target in nodes, "Unknown operation target")
            node = nodes[target]
            require(
                target in original_nodes and original_nodes[target].status not in IMMUTABLE,
                "Cannot rewrite new, completed, cancelled, or superseded nodes",
            )
            payload = PAYLOAD_TYPES[kind].model_validate(body)
            _quote(data, payload.source_quote)
            if kind == "UPDATE_NODE":
                if target == request.loop.root_node_id:
                    _changed_requirement(data, target, payload.source_quote)
                for name, value in payload.model_dump(
                    exclude={"source_quote"}, exclude_unset=True
                ).items():
                    setattr(node, name, value)
            elif kind == "UPDATE_DEADLINE":
                node.deadline = payload.deadline
                changed_nodes.add(target)
            elif kind == "SUPERSEDE_NODE":
                require(target != request.loop.root_node_id, "Root identity cannot be superseded")
                require(target not in replacements, "Node superseded more than once")
                replacement = payload.replacement_node_id
                require(
                    replacement in nodes and replacement not in original_nodes,
                    "Supersession requires a newly added replacement node",
                )
                require(
                    nodes[replacement].owner != node.owner, "Owner replacement must change owner"
                )
                replacements[target] = replacement
                node.status = NodeStatus.SUPERSEDED
                changed_nodes.add(target)
            elif kind == "CANCEL_NODE":
                require(target != request.loop.root_node_id, "Root identity cannot be cancelled")
                _changed_requirement(data, target, payload.source_quote)
                node.status = NodeStatus.CANCELLED
                changed_nodes.add(target)
    require(len(action_markers) == len(set(action_markers)), "Duplicate ADD_ACTION marker")
    require(
        not action_markers or set(action_markers) == {a.id for a in response.proposed_actions},
        "If used, ADD_ACTION markers must match all proposals exactly",
    )
    for old_id, new_id in replacements.items():
        require(
            nodes[old_id].model_dump(exclude={"status", "updated_at"})
            == original_nodes[old_id].model_dump(exclude={"status", "updated_at"}),
            "Supersession must preserve the old node's historical fields",
        )
        old_requirements = [r for r in data.context.requirements if r.node_id == old_id]
        new_requirements = [r for r in context.requirements if r.node_id == new_id]
        require(
            [r for r in context.requirements if r.node_id == old_id] == old_requirements,
            "Supersession must preserve historical evidence requirements",
        )

        def signature(requirement):
            return canonical(
                requirement.model_dump(exclude={"id", "node_id", "created_at", "description"})
            )

        require(
            sorted(map(signature, old_requirements)) == sorted(map(signature, new_requirements)),
            "Owner replacement must preserve evidence criteria; change requirements separately",
        )
        require(
            nodes[new_id].metadata == original_nodes[old_id].metadata,
            "Owner replacement must preserve identity metadata",
        )
        require(
            set(nodes[new_id].depends_on) == set(original_nodes[old_id].depends_on),
            "Owner replacement must preserve prerequisites",
        )
        for original in data.request.nodes:
            if old_id in original.depends_on and original.status not in INACTIVE:
                require(
                    new_id in nodes[original.id].depends_on
                    and old_id not in nodes[original.id].depends_on,
                    "Owner replacement must rewire every dependent and remove its old dependency",
                )
                if nodes[original.id].metadata.get("deadline_prerequisite_node_id") == old_id:
                    nodes[original.id].metadata["deadline_prerequisite_node_id"] = new_id
    for edge in request.edges:
        if (
            edge.id not in {e.id for e in data.request.edges}
            and edge.relationship == EdgeType.SUPERSEDES
        ):
            require(
                replacements.get(edge.target_node_id) == edge.source_node_id,
                "SUPERSEDES edge must match the actual owner replacement",
            )
    for node in request.nodes:
        if node.status not in IMMUTABLE and (
            node.id not in original_nodes
            or set(node.depends_on) != set(original_nodes[node.id].depends_on)
        ):
            node.status = (
                NodeStatus.BLOCKED
                if any(nodes[d].status != NodeStatus.VERIFIED for d in node.depends_on)
                else NodeStatus.ACTIVE
            )
    _validate_state(candidate)
    validate_repair_actions(
        data, candidate, response.proposed_actions, changed_nodes, terminal_loop
    )
    for action in response.proposed_actions:
        context.actions.append(action.model_copy(deep=True))
        nodes[action.node_id].action_ids.append(action.id)
    for node in request.nodes:
        old = original_nodes.get(node.id)
        if old and node != old:
            require(mutation_time >= old.updated_at, "Repair clock predates stored node state")
            node.updated_at = mutation_time
    for action in context.actions:
        old = next((a for a in data.context.actions if a.id == action.id), None)
        if old and action != old:
            require(mutation_time >= old.updated_at, "Repair clock predates stored action state")
            action.updated_at = mutation_time
    if response.operations or response.proposed_actions:
        require(mutation_time >= request.loop.updated_at, "Repair clock predates loop state")
        request.loop.updated_at = mutation_time
    _validate_state(candidate)
    return candidate
