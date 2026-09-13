"""Event endpoints.

    POST /events        manual injection, for demos and tests
    GET  /events        recent Events -- the handoff to LangGraph

All four apps are polled (events/poller.py), so there is no inbound webhook
receiver: nothing needs a public URL and there is no tunnel to keep alive.
"""

from fastapi import APIRouter, Request

from app.events.normalizer import normalize
from app.events.sink import emit, recent
from app.graph.schemas import EventType

router = APIRouter()


@router.post("/events")
async def inject(request: Request) -> dict:
    """Manual injection.

    Deliberately goes through the same normalizer and the same dedup as a
    polled item, so a demo exercises the real pipeline rather than a fake one
    running beside it.
    """
    body = await request.json()

    try:
        event_type = EventType(body.get("event_type", "SYSTEM_EVENT"))
    except ValueError:
        return {"accepted": False, "reason": "unknown event_type"}

    event = normalize(
        body.get("source_app", "manual"),
        event_type,
        body.get("payload", body),
        source="manual",
    )
    accepted = await emit(event)
    return {"accepted": accepted, "event_id": event.id}


@router.get("/events")
async def list_events(limit: int = 20) -> dict:
    """Recent Events, newest first."""
    items = list(recent)[-limit:]
    items.reverse()
    return {
        "count": len(items),
        "events": [event.model_dump(mode="json") for event in items],
    }
