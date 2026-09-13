"""Event Normalizer — raw app payload in, validated Event out.

The last box this layer owns. Everything downstream (LangGraph, Compiler,
Verifier, Replanner) consumes the Event produced here and must never see a
raw Gmail or Slack payload.

No network and no I/O, so it can be unit tested with a recorded payload and
nothing else.
"""

import json
import uuid
from datetime import datetime, timezone

from app.graph.schemas import Event, EventType

# Composio trigger slug -> event type. Unknown slugs become SYSTEM_EVENT
# rather than being dropped, so nothing disappears silently.
TRIGGER_EVENT_TYPES = {
    "GMAIL_NEW_GMAIL_MESSAGE": EventType.MESSAGE_RECEIVED,
    "SLACK_RECEIVE_MESSAGE": EventType.MESSAGE_RECEIVED,
    "SLACK_CHANNEL_MESSAGE_RECEIVED": EventType.MESSAGE_RECEIVED,
    "SLACK_DIRECT_MESSAGE_RECEIVED": EventType.MESSAGE_RECEIVED,
    "GOOGLEDRIVE_FILE_CREATED_TRIGGER": EventType.DOCUMENT_CREATED,
    "GOOGLEDRIVE_FILE_UPDATED_TRIGGER": EventType.DOCUMENT_UPDATED,
    "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_CREATED_TRIGGER": EventType.CALENDAR_EVENT_CREATED,
    "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_UPDATED_TRIGGER": EventType.CALENDAR_EVENT_UPDATED,
    "GOOGLECALENDAR_EVENT_CANCELED_DELETED_TRIGGER": EventType.CALENDAR_EVENT_DELETED,
}


def pick(payload: dict, *names, default=None):
    """First present key wins. Field names vary by app and SDK version."""
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return default


# --- per-app extraction -------------------------------------------------
# The only code in the project that knows what a raw payload looks like.


def from_gmail(payload: dict) -> dict:
    # Field names verified against a live GMAIL_FETCH_EMAILS response.
    return {
        "external_id": pick(payload, "messageId", "message_id", "id"),
        "actor": pick(payload, "sender", "from", "from_email"),
        "subject": pick(payload, "subject"),
        "content": pick(payload, "messageText", "message_text", "snippet", default=""),
        "attachments": pick(payload, "attachmentList", "attachments", default=[]) or [],
        "timestamp": pick(payload, "messageTimestamp", "message_timestamp"),
        "metadata": {
            "thread_id": pick(payload, "threadId", "thread_id"),
            "to": pick(payload, "to", "recipient"),
            "labels": pick(payload, "labelIds", default=[]),
            "link": pick(payload, "display_url"),
        },
    }


def from_slack(payload: dict) -> dict:
    # UNVERIFIED against live data -- check data/events.jsonl on first event.
    return {
        "external_id": pick(payload, "ts", "event_ts", "client_msg_id"),
        "actor": pick(payload, "user", "user_id", "username"),
        "subject": None,
        "content": pick(payload, "text", default=""),
        "attachments": pick(payload, "files", default=[]) or [],
        "timestamp": None,
        "metadata": {
            "channel_id": pick(payload, "channel", "channel_id"),
            "thread_ts": pick(payload, "thread_ts"),
        },
    }


def from_drive(payload: dict) -> dict:
    # UNVERIFIED against live data.
    name = pick(payload, "name", "title", "file_name", default="")
    return {
        "external_id": pick(payload, "id", "file_id", "fileId"),
        "actor": pick(payload, "owner", "last_modifying_user"),
        "subject": name,
        "content": f"File: {name}",
        "attachments": [],
        "timestamp": pick(payload, "createdTime", "modifiedTime"),
        "metadata": {
            "mime_type": pick(payload, "mimeType", "mime_type"),
            "link": pick(payload, "webViewLink", "web_view_link"),
        },
    }


def from_calendar(payload: dict) -> dict:
    # UNVERIFIED against live data.
    title = pick(payload, "summary", "title", default="")
    return {
        "external_id": pick(payload, "id", "event_id"),
        "actor": pick(payload, "organizer", "creator"),
        "subject": title,
        "content": pick(payload, "description", default=title),
        "attachments": [],
        "timestamp": pick(payload, "updated", "created"),
        "metadata": {
            "start": pick(payload, "start", "start_time"),
            "end": pick(payload, "end", "end_time"),
        },
    }


EXTRACTORS = {
    "GMAIL": from_gmail,
    "SLACK": from_slack,
    "GOOGLEDRIVE": from_drive,
    "GOOGLECALENDAR": from_calendar,
}


def _parse_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def normalize(toolkit: str, event_type: EventType, payload: dict, source: str) -> Event:
    """Build a validated Event. Never raises on an unknown app."""
    extract = EXTRACTORS.get((toolkit or "").upper())
    if extract:
        fields = extract(payload)
    else:
        fields = {
            "external_id": None,
            "actor": None,
            "subject": None,
            "content": json.dumps(payload, default=str)[:500],
            "attachments": [],
            "timestamp": None,
            "metadata": {"unmapped_toolkit": toolkit},
        }

    return Event(
        id=f"event_{uuid.uuid4().hex[:12]}",
        source_app=(toolkit or "unknown").lower(),
        event_type=event_type,
        external_id=fields["external_id"],
        timestamp=_parse_timestamp(fields.get("timestamp")),
        actor=fields["actor"],
        subject=fields["subject"],
        content=fields["content"],
        attachments=fields["attachments"],
        metadata={**fields["metadata"], "source": source},
    )


def from_composio_trigger(body: dict) -> Event:
    """Composio webhook body -> Event."""
    toolkit = body.get("toolkit_slug") or body.get("toolkitSlug") or ""
    trigger_slug = body.get("trigger_slug") or body.get("triggerSlug") or ""
    payload = body.get("payload") or body.get("data") or {}

    event = normalize(
        toolkit,
        TRIGGER_EVENT_TYPES.get(trigger_slug, EventType.SYSTEM_EVENT),
        payload,
        source="webhook",
    )
    event.metadata["trigger_slug"] = trigger_slug
    return event
