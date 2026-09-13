"""Repair proposal policy and mandatory cleanup of obsolete pending work."""

from pydantic import AwareDatetime, TypeAdapter

from app.constants import ActionStatus, NodeStatus, RiskLevel
from app.graph.compiler_validation import _PARAMETERS, _literal_in_text
from app.graph.repair_validation import CANCELLABLE, INACTIVE, action_key, require
from app.graph.semantic_validation import ACTION_POLICY

_TIMESTAMP = TypeAdapter(AwareDatetime)


def validate_repair_actions(original, candidate, proposals, changed_nodes, terminal_loop):
    nodes = {n.id: n for n in candidate.request.nodes}
    previous = {a.id: a for a in original.context.actions}
    current = {a.id: a for a in candidate.context.actions}
    event = original.request.triggering_event
    keys, ids, external_writes = set(), set(), set()
    params_spec = _PARAMETERS | {
        "UPDATE_CALENDAR_EVENT": ({"event_id", "title", "start", "end"}, set()),
        "CANCEL_CALENDAR_EVENT": ({"event_id"}, set()),
    }
    cleanup_nodes = {n.id for n in nodes.values() if n.status in INACTIVE | {NodeStatus.VERIFIED}}
    for action in proposals:
        require(action.id not in previous and action.id not in ids, "Duplicate proposed action ID")
        require(
            action.idempotency_key == f"{candidate.request.loop.id}:{action.id}",
            "Invalid action idempotency key",
        )
        ids.add(action.id)
        key = action_key(action)
        require(key not in keys, "Duplicate semantic action proposal")
        keys.add(key)
        if action.action_type in {"CREATE_CALENDAR_EVENT", "SAVE_DRIVE_FILE"}:
            require(
                not any(
                    action_key(old) == key and old.status == ActionStatus.VERIFIED
                    for old in current.values()
                ),
                "Persistent external result already exists; do not recreate it",
            )
        require(
            not any(
                action_key(old) == key and old.status in CANCELLABLE - {ActionStatus.FAILED}
                for old in current.values()
            ),
            "Equivalent pending action already exists",
        )
        require(
            action.loop_id == candidate.request.loop.id and action.node_id in nodes,
            "Invalid action loop or node",
        )
        require(
            action.external_id is None and action.error is None,
            "Proposal cannot claim external execution",
        )
        policy = ACTION_POLICY.get(action.action_type)
        require(policy is not None, "Unsupported repair action")
        app, risk = policy
        require(
            action.app == app and app in candidate.context.available_apps,
            "Unavailable/wrong action app",
        )
        require(action.risk_level == risk, "Action risk must match shared policy")
        require(
            action.requires_approval or risk != RiskLevel.MEDIUM, "External write needs approval"
        )
        require(
            action.status
            == (
                ActionStatus.AWAITING_APPROVAL
                if action.requires_approval
                else ActionStatus.PROPOSED
            ),
            "Proposal must await required approval",
        )
        node = nodes[action.node_id]
        cancel = action.action_type == "CANCEL_CALENDAR_EVENT"
        require(not terminal_loop or cancel, "Closed loops permit external cleanup only")
        require(
            node.id not in cleanup_nodes or cancel, "No new work for resolved or superseded nodes"
        )
        if node.status in {NodeStatus.BLOCKED, NodeStatus.PENDING}:
            require(
                action.action_type
                in {"CREATE_CALENDAR_EVENT", "UPDATE_CALENDAR_EVENT", "CANCEL_CALENDAR_EVENT"},
                "Action prerequisites are not satisfied",
            )
        required, optional = params_spec[action.action_type]
        params = action.parameters
        require(
            required <= params.keys() and params.keys() <= required | optional,
            "Action parameter keys do not match the proposal convention",
        )
        require(
            all(isinstance(v, str) and v.strip() for v in params.values()),
            "Action parameters must be nonempty strings",
        )
        method = "API_STATE" if action.action_type.startswith("SEARCH_") else "READ_AFTER_WRITE"
        require(action.verification_method == method, "Missing action verification method")
        if action.action_type in {"CREATE_CALENDAR_EVENT", "UPDATE_CALENDAR_EVENT"}:
            start = _TIMESTAMP.validate_python(params["start"])
            end = _TIMESTAMP.validate_python(params["end"])
            require(
                node.deadline is not None and start == node.deadline and end > start,
                "Calendar checkpoint must start at the repaired deadline with a later end",
            )
        if action.action_type in {"UPDATE_CALENDAR_EVENT", "CANCEL_CALENDAR_EVENT"}:
            require(
                params["event_id"] not in external_writes,
                "Conflicting writes to one calendar event",
            )
            external_writes.add(params["event_id"])
            require(
                any(
                    old.external_id == params["event_id"]
                    and old.node_id == action.node_id
                    and old.app == "google_calendar"
                    and old.status == ActionStatus.VERIFIED
                    and old.action_type in {"CREATE_CALENDAR_EVENT", "UPDATE_CALENDAR_EVENT"}
                    for old in previous.values()
                ),
                "Calendar target must come from verified action history",
            )
        elif action.action_type == "SEND_EMAIL":
            recipient = params["to"]
            require(
                recipient.count("@") == 1
                and not any(c.isspace() or c in ",;<>" for c in recipient),
                "Email needs one literal address",
            )
            texts = [event.actor or "", event.subject or "", event.content or ""]
            known = [
                old.parameters.get("to")
                for old in previous.values()
                if old.action_type == "SEND_EMAIL" and old.node_id == node.id
            ]
            require(
                recipient in known or any(_literal_in_text(recipient, t) for t in texts),
                "Email recipient must be observed or already bound to this node",
            )
        elif action.action_type == "SEND_SLACK_MESSAGE":
            metadata = event.metadata if event.source_app == "slack" else {}
            require(params["channel_id"] == metadata.get("channel_id"), "Unknown Slack channel")
            require(
                "thread_ts" not in params or params["thread_ts"] == metadata.get("thread_ts"),
                "Unknown Slack thread",
            )
        elif action.action_type == "SAVE_DRIVE_FILE":
            require(
                any(
                    params["attachment_id"] in (a.get("id"), a.get("attachment_id"))
                    for a in event.attachments
                ),
                "Unknown source attachment",
            )

    affected = changed_nodes | cleanup_nodes
    if terminal_loop:
        affected |= set(nodes)
    for old in previous.values():
        if old.node_id not in affected and not (terminal_loop and old.node_id is None):
            continue
        require(old.status != ActionStatus.EXECUTING, "Reconcile in-flight actions before repair")
        if old.status in CANCELLABLE and old.external_id is None:
            require(
                current[old.id].status == ActionStatus.CANCELLED,
                "Repair must cancel obsolete pending actions on changed/resolved nodes",
            )
        if (
            old.app == "google_calendar"
            and old.status == ActionStatus.VERIFIED
            and old.external_id
            and old.action_type in {"CREATE_CALENDAR_EVENT", "UPDATE_CALENDAR_EVENT"}
        ):
            # A previous verified cancellation is already sufficient; retain all history.
            cancelled = any(
                a.action_type == "CANCEL_CALENDAR_EVENT"
                and a.status == ActionStatus.VERIFIED
                and a.parameters.get("event_id") == old.external_id
                for a in previous.values()
            )
            if cancelled:
                continue
            allowed = (
                {"CANCEL_CALENDAR_EVENT"}
                if old.node_id in cleanup_nodes or terminal_loop
                else {"CANCEL_CALENDAR_EVENT", "UPDATE_CALENDAR_EVENT"}
            )
            require(
                any(
                    a.action_type in allowed and a.parameters.get("event_id") == old.external_id
                    for a in [*proposals, *current.values()]
                    if a.status
                    in {
                        ActionStatus.PROPOSED,
                        ActionStatus.AWAITING_APPROVAL,
                        ActionStatus.APPROVED,
                    }
                ),
                "Repair needs approval-gated cleanup/update of the verified Calendar checkpoint",
            )
