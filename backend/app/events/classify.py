"""Decide which event type a raw item represents.

A polled item is not always "created" -- the same Calendar event can arrive
as a creation, an edit, or a cancellation. Only the payload can tell them
apart, so that decision lives here rather than being fixed per source.
"""

from app.graph.schemas import EventType


def gmail(item: dict) -> EventType:
    # A Gmail message's whole lifecycle is in its labels. TRASH wins over
    # everything: a deleted sent message is a deletion, not a send.
    labels = item.get("labelIds") or item.get("label_ids") or []
    if "TRASH" in labels:
        return EventType.MESSAGE_DELETED
    if "SENT" in labels:
        return EventType.MESSAGE_SENT
    return EventType.MESSAGE_RECEIVED


def slack(item: dict) -> EventType:
    # Set by composio.fetch_recent_slack, which diffs consecutive polls --
    # Slack's search results say nothing about edits or deletions on their own.
    state = item.get("slack_state")
    if state == "deleted":
        return EventType.MESSAGE_DELETED
    if state == "edited":
        return EventType.MESSAGE_UPDATED
    return EventType.MESSAGE_RECEIVED


def drive(item: dict) -> EventType:
    # Two different kinds of gone, and both count as deleted:
    #   removed  file destroyed outright, or shared access revoked -- there is
    #            no file object left, only an id
    #   trashed  moved to the bin, still recoverable
    if item.get("removed") or item.get("trashed"):
        return EventType.DOCUMENT_DELETED

    created = item.get("createdTime")
    modified = item.get("modifiedTime")
    # Google sets both on creation and they match to the millisecond; an edit
    # moves modifiedTime on.
    if created and modified and created != modified:
        return EventType.DOCUMENT_UPDATED
    return EventType.DOCUMENT_CREATED


def calendar(item: dict) -> EventType:
    # Google keeps deleted events and marks them cancelled -- they only come
    # back at all when the query passes showDeleted.
    if item.get("status") == "cancelled":
        return EventType.CALENDAR_EVENT_DELETED

    created = item.get("created")
    updated = item.get("updated")
    # Google always sets both; they differ once the event has been edited.
    if created and updated and created[:19] != updated[:19]:
        return EventType.CALENDAR_EVENT_UPDATED
    return EventType.CALENDAR_EVENT_CREATED
