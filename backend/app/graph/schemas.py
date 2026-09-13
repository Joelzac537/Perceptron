"""Shared Pydantic schemas.

OWNERSHIP NOTE: this file is shared across the team. Only the Event side is
defined here, contributed by the integrations layer. Loop, OutcomeNode, Edge,
Evidence and Action belong to whoever owns the runtime -- add them here rather
than creating a second schemas module.

Event is the contract boundary. Everything upstream (Composio, Gmail, Slack)
is this layer's problem; everything downstream only ever sees an Event.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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


class Event(BaseModel):
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
