"""Event Normalizer — raw app payload in, validated Event out.

The last box this layer owns. Everything downstream (LangGraph, Compiler,
Verifier, Replanner) consumes the Event produced here and must never see a
raw Gmail or Slack payload.

No network and no I/O, so it can be unit tested with a recorded payload and
nothing else.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from app.graph.schemas import Event, EventType


def pick(payload: dict, *names, default=None):
    """First present key wins. Field names vary by app and SDK version."""
    for name in names:
        if name in payload and payload[name] not in (None, ""):
            return payload[name]
    return default


def as_actor(value) -> str | None:
    """Coerce an actor to a string.

    Google returns organizer/creator as {"email": ..., "displayName": ...},
    but Event.actor is a str -- handing the dict straight through fails
    Pydantic validation.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("email") or value.get("displayName") or value.get("name")
    return str(value)


# --- per-app extraction -------------------------------------------------
# The only code in the project that knows what a raw payload looks like.


def from_gmail(payload: dict) -> dict:
    # Field names verified against a live GMAIL_FETCH_EMAILS response.
    labels = pick(payload, "labelIds", "label_ids", default=[]) or []

    # The mailbox a message currently sits in, and the only thing about a mail
    # that ever changes. It goes in the version -- see Event.dedup_key -- so
    # that deleting a message we already reported is seen as a new event
    # rather than discarded as a duplicate of its own arrival.
    box = "trash" if "TRASH" in labels else "sent" if "SENT" in labels else "inbox"

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
            "labels": labels,
            "link": pick(payload, "display_url"),
            "version": box,
        },
    }


def from_slack(payload: dict) -> dict:
    # Verified against SLACK_FETCH_CONVERSATION_HISTORY: ts, user, text.
    # channel is not on the message itself when read from history -- it comes
    # from the request there, and from the payload in a trigger.
    return {
        "external_id": pick(payload, "ts", "event_ts", "client_msg_id"),
        "actor": as_actor(pick(payload, "username", "user", "user_id")),
        "subject": None,
        "content": pick(payload, "text", default=""),
        "attachments": pick(payload, "files", default=[]) or [],
        # ts is unix seconds as a string -- the message's real send time,
        # which beats "when we happened to poll".
        "timestamp": _from_unix(pick(payload, "ts", "event_ts")),
        "metadata": {
            "channel_id": pick(payload, "channel", "channel_id"),
            "channel_name": pick(payload, "channel_name"),
            "user_id": pick(payload, "user_id", "user"),
            "thread_ts": pick(payload, "thread_ts"),
            # State plus a digest of the text -- see Event.dedup_key. ts never
            # changes when a message is edited, so the text itself has to be
            # part of the identity or the edit is dropped as a duplicate.
            "version": _slack_version(payload),
        },
    }


def _slack_version(payload: dict) -> str:
    state = payload.get("slack_state") or "live"
    digest = hashlib.sha1(str(payload.get("text") or "").encode()).hexdigest()[:10]
    return f"{state}|{digest}"


# mime type -> a word the reasoning layer can actually use. Matching on raw
# mime strings downstream would spread Google-specific trivia (the
# application/vnd.google-apps.* family) through code that should not know it.
DRIVE_CATEGORIES = [
    ("application/pdf", "pdf"),
    ("application/vnd.google-apps.document", "document"),
    ("application/vnd.google-apps.spreadsheet", "spreadsheet"),
    ("application/vnd.google-apps.presentation", "presentation"),
    ("application/vnd.google-apps.folder", "folder"),
    ("application/vnd.openxmlformats-officedocument.wordprocessing", "document"),
    ("application/vnd.openxmlformats-officedocument.spreadsheet", "spreadsheet"),
    ("application/vnd.openxmlformats-officedocument.presentation", "presentation"),
    ("image/", "image"),
    ("text/", "text"),
]


def drive_category(mime: str | None) -> str:
    """Coarse file kind. A receipt is a pdf or an image; both count as evidence."""
    mime = str(mime or "")
    for prefix, category in DRIVE_CATEGORIES:
        if mime.startswith(prefix):
            return category
    return "other"


def from_drive(payload: dict) -> dict:
    # Fed by the Drive change feed (see composio.fetch_recent_drive), which
    # flattens each change into a file dict plus `removed` and `change_time`.
    # Note the link field is display_url, not webViewLink.
    #
    # A permanently deleted file arrives with an id and nothing else -- no
    # name, no mimeType -- so every field here has to tolerate being absent.
    removed = bool(payload.get("removed"))
    trashed = bool(payload.get("trashed"))
    name = pick(payload, "name", "title", "file_name", default="")

    stamp = pick(payload, "change_time", "modifiedTime", "createdTime") or ""
    # State belongs in the version -- see Event.dedup_key. Moving a file to the
    # bin does not reliably change its modifiedTime, so a timestamp alone gives
    # the deletion the same key as the creation and the deletion is dropped as
    # a duplicate.
    state = "removed" if removed else "trashed" if trashed else "live"

    return {
        "external_id": pick(payload, "id", "file_id", "fileId"),
        "actor": as_actor(pick(payload, "owner", "last_modifying_user")),
        "subject": name or ("(deleted file)" if removed else ""),
        # "text" is only present if content extraction is switched on -- see
        # composio.drive_file_text. Until then this is the filename, which is
        # enough to say a receipt was uploaded but not what it says.
        "content": pick(payload, "text")
        or (f"File: {name}" if name else f"File {pick(payload, 'id')} deleted"),
        "attachments": [],
        # change_time first: for a permanent delete it is the only timestamp
        # that exists at all.
        "timestamp": stamp or None,
        "metadata": {
            "mime_type": pick(payload, "mimeType", "mime_type"),
            "category": drive_category(pick(payload, "mimeType", "mime_type")),
            "link": pick(payload, "display_url", "webViewLink", "web_view_link"),
            "trashed": trashed,
            "removed": removed,
            "version": f"{stamp}|{state}",
        },
    }


def from_calendar(payload: dict) -> dict:
    # Verified against GOOGLECALENDAR_EVENTS_LIST. organizer and creator are
    # objects, not strings -- see as_actor. start/end are objects too, so they
    # stay in metadata rather than being flattened.
    title = pick(payload, "summary", "title", default="")
    return {
        "external_id": pick(payload, "id", "event_id"),
        # EVENTS_LIST returns organizer/creator as objects; some payloads
        # carry a flat organizer_email instead.
        "actor": as_actor(
            pick(payload, "organizer", "creator", "organizer_email", "organizer_name")
        ),
        "subject": title,
        "content": pick(payload, "description", default=title),
        "attachments": [],
        "timestamp": pick(payload, "updated", "created"),
        "metadata": {
            "start": pick(payload, "start", "start_time"),
            "end": pick(payload, "end", "end_time"),
            "link": pick(payload, "display_url", "htmlLink"),
            "status": pick(payload, "status"),
            # Change stamp -- see Event.dedup_key. Without this an edit or a
            # deletion looks identical to the original creation.
            "version": pick(payload, "updated", "created"),
        },
    }


EXTRACTORS = {
    "GMAIL": from_gmail,
    "SLACK": from_slack,
    "GOOGLEDRIVE": from_drive,
    "GOOGLECALENDAR": from_calendar,
}


def _from_unix(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


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

