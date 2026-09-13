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
    DOCUMENT_CREATED = "DOCUMENT_CREATED"
    DOCUMENT_UPDATED = "DOCUMENT_UPDATED"
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
        """Identity for deduplication: source_app + external_id.

        Webhooks can be delivered more than once and the Gmail poller sees the
        same message every pass. One real-world event, one Event.
        """
        return f"{self.source_app}:{self.external_id or self.id}"
