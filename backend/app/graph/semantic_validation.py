"""Pure validation of initial compiler proposals; never mutates or executes them.

This checks graph invariants, not whether the natural-language goal was correctly
understood. Existing/repaired graphs need the separate A5 repair validator.
"""

from graphlib import CycleError, TopologicalSorter

from app.constants import ActionStatus, EdgeType, LoopStatus, NodeStatus, RiskLevel
from app.graph.schemas import CompiledGraph, CompileGoalRequest

# Initial MVP proposal vocabulary from the use cases and stack reference.
# B/C must coordinate additions with this validation boundary.
ACTION_POLICY = {
    "SEARCH_GMAIL": ("gmail", RiskLevel.LOW),
    "SEARCH_DRIVE": ("google_drive", RiskLevel.LOW),
    "SAVE_DRIVE_FILE": ("google_drive", RiskLevel.LOW),
    "CREATE_CALENDAR_EVENT": ("google_calendar", RiskLevel.LOW),
    "UPDATE_CALENDAR_EVENT": ("google_calendar", RiskLevel.MEDIUM),
    "CANCEL_CALENDAR_EVENT": ("google_calendar", RiskLevel.MEDIUM),
    "SEND_EMAIL": ("gmail", RiskLevel.MEDIUM),
    "SEND_SLACK_MESSAGE": ("slack", RiskLevel.MEDIUM),
}


class GraphValidationError(ValueError):
    """All detected proposal issues, suitable for a bounded model repair attempt."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issues))


def validate_compiled_graph(
    graph: CompiledGraph, request: CompileGoalRequest | None = None
) -> CompiledGraph:
    """Return the unchanged proposal or raise with deterministic validation issues."""
    issues: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            issues.append(message)

    def unique(values: list[str], label: str) -> None:
        check(len(values) == len(set(values)), f"Duplicate {label}")

    nodes = {node.id: node for node in graph.nodes}
    requirements = {requirement.id: requirement for requirement in graph.evidence_requirements}
    actions = {action.id: action for action in graph.proposed_actions}
    loop_id = graph.loop.id
    for label, objects in (
        ("node IDs", graph.nodes),
        ("edge IDs", graph.edges),
        ("requirement IDs", graph.evidence_requirements),
        ("action IDs", graph.proposed_actions),
    ):
        unique([obj.id for obj in objects], label)
    unique(graph.loop.node_ids, "loop.node_ids")
    unique(graph.loop.source_event_ids, "source event IDs")
    unique([action.idempotency_key for action in graph.proposed_actions], "idempotency keys")
    check(graph.loop.root_node_id in nodes, "Root node missing")
    check(set(graph.loop.node_ids) == set(nodes), "loop.node_ids must match graph nodes")
    check(
        graph.loop.status in {LoopStatus.ACTIVE, LoopStatus.WAITING, LoopStatus.BLOCKED},
        "Initial loop must be unresolved",
    )
    check(graph.loop.completed_at is None, "Initial loop cannot have completed_at")
    check(graph.loop.updated_at >= graph.loop.created_at, "Loop timestamps are reversed")
    if graph.clarification_needed:
        check(not actions, "Clarification must be resolved before proposing executable actions")
    if request is not None:
        check(graph.loop.user_id == request.user_id, "Compiler changed user identity")
        expected_sources = {request.source_event.id} if request.source_event else set()
        check(set(graph.loop.source_event_ids) == expected_sources, "Source event IDs mismatch")

    dependencies: dict[str, set[str]] = {}
    expected_edges: set[tuple[str, str]] = set()
    for node in graph.nodes:
        check(node.loop_id == loop_id, f"Node {node.id} belongs to another loop")
        check(node.updated_at >= node.created_at, f"Node {node.id} timestamps are reversed")
        check(
            node.status
            in {NodeStatus.PENDING, NodeStatus.ACTIVE, NodeStatus.WAITING, NodeStatus.BLOCKED},
            f"Initial node {node.id} must be unresolved",
        )
        check(not node.evidence_ids, f"Initial node {node.id} cannot claim assessed evidence")
        for label, values in (
            ("dependencies", node.depends_on),
            ("requirements", node.evidence_requirement_ids),
            ("actions", node.action_ids),
        ):
            unique(values, f"{label} on {node.id}")
        dependencies[node.id] = set(node.depends_on)
        for dependency in node.depends_on:
            check(dependency in nodes, f"Unknown dependency {dependency} on {node.id}")
            check(dependency != node.id, f"Self-dependency on {node.id}")
            expected_edges.add((node.id, dependency))
        if node.depends_on:
            check(
                node.status in {NodeStatus.PENDING, NodeStatus.BLOCKED},
                f"Node {node.id} has unresolved prerequisites and cannot be active/waiting",
            )
        check(bool(node.evidence_requirement_ids), f"Node {node.id} needs an evidence requirement")
        for requirement_id in node.evidence_requirement_ids:
            requirement = requirements.get(requirement_id)
            check(
                requirement is not None and requirement.node_id == node.id,
                f"Invalid requirement reference {requirement_id} on {node.id}",
            )
        for action_id in node.action_ids:
            action = actions.get(action_id)
            check(
                action is not None and action.node_id == node.id,
                f"Invalid action reference {action_id} on {node.id}",
            )

    for requirement in graph.evidence_requirements:
        node = nodes.get(requirement.node_id)
        check(
            node is not None and requirement.id in node.evidence_requirement_ids,
            f"Orphan evidence requirement {requirement.id}",
        )

    actual_edges: set[tuple[str, str]] = set()
    semantic_edges: set[tuple[str, str, EdgeType]] = set()
    for edge in graph.edges:
        check(edge.loop_id == loop_id, f"Edge {edge.id} belongs to another loop")
        check(
            edge.source_node_id in nodes and edge.target_node_id in nodes,
            f"Edge {edge.id} references an unknown node",
        )
        key = (edge.source_node_id, edge.target_node_id, edge.relationship)
        check(key not in semantic_edges, f"Duplicate semantic edge {edge.id}")
        semantic_edges.add(key)
        if edge.relationship == EdgeType.DEPENDS_ON:
            actual_edges.add((edge.source_node_id, edge.target_node_id))
    check(actual_edges == expected_edges, "DEPENDS_ON edges disagree with depends_on arrays")
    try:
        tuple(TopologicalSorter(dependencies).static_order())
    except CycleError:
        issues.append("Dependency cycle detected")

    reachable: set[str] = set()
    pending = [graph.loop.root_node_id]
    while pending:
        current = pending.pop()
        if current not in reachable:
            reachable.add(current)
            pending.extend(dependencies.get(current, set()))
    check(set(nodes) <= reachable, "Nodes are disconnected from the root dependency graph")

    for action in graph.proposed_actions:
        check(action.loop_id == loop_id, f"Action {action.id} belongs to another loop")
        check(action.updated_at >= action.created_at, f"Action {action.id} timestamps are reversed")
        if action.node_id is not None:
            node = nodes.get(action.node_id)
            check(
                node is not None and action.id in node.action_ids,
                f"Invalid node reference on action {action.id}",
            )
        check(
            action.status in {ActionStatus.PROPOSED, ActionStatus.AWAITING_APPROVAL},
            f"Action {action.id} must be a proposal, not an executed or approved action",
        )
        check(action.external_id is None, f"Action {action.id} cannot claim an external result")
        check(action.risk_level != RiskLevel.HIGH, f"High-risk action {action.id} is outside MVP")
        policy = ACTION_POLICY.get(action.action_type)
        check(policy is not None, f"Unsupported action type {action.action_type}")
        if policy:
            app, minimum_risk = policy
            check(action.app == app, f"Action {action.id} has incorrect app")
            if minimum_risk == RiskLevel.MEDIUM:
                check(action.risk_level == RiskLevel.MEDIUM, f"Action {action.id} must be MEDIUM")
                check(action.requires_approval, f"Action {action.id} requires approval")
        if action.risk_level == RiskLevel.MEDIUM:
            check(action.requires_approval, f"Medium-risk action {action.id} requires approval")
        if action.requires_approval:
            check(
                action.status == ActionStatus.AWAITING_APPROVAL,
                f"Action {action.id} must await approval",
            )
        else:
            check(action.status == ActionStatus.PROPOSED, f"Action {action.id} must be PROPOSED")
        if request is not None:
            check(action.app in request.available_apps, f"App {action.app} is unavailable")

    if issues:
        raise GraphValidationError(issues)
    return graph
