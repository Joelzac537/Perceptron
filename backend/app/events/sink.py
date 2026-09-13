"""Where normalized Events go.

Deduplicates, records, and forwards. This is the handoff point: swap
FORWARD_URL for a direct call into the LangGraph runtime once that exists.
"""

import json
import os
from collections import deque
from collections.abc import Awaitable, Callable

from app.graph.schemas import Event
from app.integrations.composio import DATA

EVENT_LOG = DATA / "events.jsonl"
SEEN_PATH = DATA / "seen_ids.json"

FORWARD_URL = os.getenv("LOOPGRAPH_WEBHOOK_URL")

# The direct handoff into the LangGraph runtime, registered at startup. Kept as a
# callback rather than an import so this module stays free of the reasoning layer
# and the ingestion tests keep running with no model configured.
_handler: Callable[[Event], Awaitable[object]] | None = None


def set_handler(handler: "Callable[[Event], Awaitable[object]] | None") -> None:
    """Register what happens to an Event once it is accepted."""
    global _handler
    _handler = handler

# Last 200 Events, so the next stage can pull recent history over HTTP.
recent: deque[Event] = deque(maxlen=200)

# Dedup across every ingestion path, not just Gmail.
seen: set[str] = set()


def load_seen() -> None:
    if SEEN_PATH.exists():
        try:
            seen.update(json.loads(SEEN_PATH.read_text()))
        except Exception:
            pass


def save_seen() -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen)[-1000:]))


def mark_seen(event: Event) -> None:
    """Record without emitting. Used to baseline an existing inbox."""
    seen.add(event.dedup_key)


async def emit(event: Event) -> bool:
    """Record an Event and pass it on. False means duplicate, already handled."""
    if event.dedup_key in seen:
        return False
    seen.add(event.dedup_key)

    recent.append(event)
    payload = event.model_dump(mode="json")

    with EVENT_LOG.open("a") as handle:
        handle.write(json.dumps(payload) + "\n")
    save_seen()

    # Slack and Drive have no subject -- fall back to the body, or the log
    # line just reads "None" for every message that arrives.
    summary = event.subject or " ".join(str(event.content or "").split())[:70] or "(empty)"
    who = f" <{event.actor}>" if event.actor else ""
    print(f"[{event.source_app}] {event.event_type.value}{who} :: {summary}")

    if _handler is not None:
        try:
            await _handler(event)
        except Exception as exc:  # noqa: BLE001 - ingestion must outlive reasoning
            print(f"  pipeline failed: {exc}")

    if FORWARD_URL:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as http:
                await http.post(FORWARD_URL, json=payload)
        except Exception as exc:
            print(f"  forward failed: {exc}")

    return True
