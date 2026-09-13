"""Initial compiler checks beyond graph shape; app execution stays with B/C.

Literal grounding checks prevent invented routing identifiers, but cannot prove
semantic intent. Approval/executor validation and live model evaluation remain required.
"""

from datetime import datetime

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from app.constants import NodeStatus
from app.graph.schemas import CompiledGraph, CompileGoalRequest
from app.graph.semantic_validation import GraphValidationError, validate_compiled_graph

_TIMESTAMP = TypeAdapter(AwareDatetime)
_PARAMETERS = {
    "SEARCH_GMAIL": ({"query"}, set()),
    "SEARCH_DRIVE": ({"query"}, set()),
    "CREATE_CALENDAR_EVENT": ({"title", "start", "end"}, set()),
    "SAVE_DRIVE_FILE": ({"attachment_id"}, set()),
    "SEND_EMAIL": ({"to", "subject", "body"}, set()),
    "SEND_SLACK_MESSAGE": ({"channel_id", "message"}, {"thread_ts"}),
}


def _literal_in_text(value: str, text: str) -> bool:
    """Match a complete routing token; no regex parsing of model prose."""
    token_chars = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_@.+-")
    text, value = text.casefold(), value.casefold()
    start = 0
    while (offset := text.find(value, start)) >= 0:
        end = offset + len(value)
        if (offset == 0 or text[offset - 1] not in token_chars) and (
            end == len(text) or text[end] not in token_chars
        ):
            return True
        start = offset + 1
    return False


def validate_compiler_result(graph: CompiledGraph, request: CompileGoalRequest) -> CompiledGraph:
    validate_compiled_graph(graph, request)
    issues = []
    nodes = {node.id: node for node in graph.nodes}
    if not graph.clarification_needed and graph.clarification_question is not None:
        issues.append("A clarification question must set clarification_needed")
    event = request.source_event
    # Prose and the source actor can ground an email address. Metadata is not a
    # blanket allowlist: specific app identifiers below use the matching fields.
    texts = [request.user_goal or ""]
    if event:
        texts.extend([event.actor or "", event.subject or "", event.content or ""])
    allowed_sources = set(request.available_apps) | {"loopgraph"}
    for requirement in graph.evidence_requirements:
        if not set(requirement.source_apps) <= allowed_sources:
            issues.append("Evidence source must be available, or manual confirmation via loopgraph")
    for action in graph.proposed_actions:
        node = nodes.get(action.node_id)
        if node is None:
            issues.append("Initial actions must reference an outcome node")
        elif node.status in {NodeStatus.BLOCKED, NodeStatus.PENDING} and (
            action.action_type != "CREATE_CALENDAR_EVENT"
        ):
            issues.append("Defer non-calendar actions until their prerequisites are satisfied")
        spec = _PARAMETERS.get(action.action_type)
        if spec is None:
            issues.append("Initial compilation cannot update or cancel existing external state")
            continue
        required, optional = spec
        params = action.parameters
        if set(params) - required - optional or not required <= set(params):
            issues.append(f"{action.action_type} parameter keys must match the proposal convention")
            continue
        if any(not isinstance(value, str) or not value.strip() for value in params.values()):
            issues.append(f"{action.action_type} parameter values must be nonempty strings")
            continue
        method = "API_STATE" if action.action_type.startswith("SEARCH_") else "READ_AFTER_WRITE"
        if action.verification_method != method:
            issues.append(f"{action.action_type} must specify {method} verification")
        if action.action_type == "CREATE_CALENDAR_EVENT":
            try:
                start: datetime = _TIMESTAMP.validate_python(params["start"])
                end: datetime = _TIMESTAMP.validate_python(params["end"])
                if end <= start:
                    issues.append("Calendar end must be after start")
                if node and node.deadline is None:
                    issues.append("A Calendar checkpoint needs a known node deadline")
            except ValidationError:
                issues.append("Calendar start and end must be timezone-aware timestamps")
        elif action.action_type == "SEND_EMAIL":
            address = params["to"]
            if address.count("@") != 1 or any(c.isspace() or c in ",;<>" for c in address):
                issues.append("Email to must contain exactly one address")
            elif not any(_literal_in_text(address, text) for text in texts):
                issues.append("Email recipient must be explicitly present in the input")
        elif action.action_type == "SEND_SLACK_MESSAGE":
            metadata = event.metadata if event and event.source_app == "slack" else {}
            if params["channel_id"] != metadata.get("channel_id"):
                issues.append("Slack channel_id must come from source Slack metadata")
            if "thread_ts" in params and params["thread_ts"] != metadata.get("thread_ts"):
                issues.append("Slack thread_ts must come from source Slack metadata")
        elif action.action_type == "SAVE_DRIVE_FILE":
            ids = {
                item[key]
                for item in (event.attachments if event else [])
                for key in ("id", "attachment_id")
                if isinstance(item.get(key), str)
            }
            if params["attachment_id"] not in ids:
                issues.append(
                    "Drive save must reference an attachment supplied by the source event"
                )
    if issues:
        raise GraphValidationError(issues)
    return graph
