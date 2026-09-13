"""Ingestion endpoints.

    POST /webhooks/composio   Slack, Drive, Calendar arrive here
    POST /events              manual injection, for demos and tests
    GET  /events              recent Events -- the handoff to LangGraph
"""

import json
import os

from fastapi import APIRouter, Request

from app.events.normalizer import from_composio_trigger, normalize
from app.events.sink import emit, recent
from app.graph.schemas import EventType
from app.integrations.composio import client

router = APIRouter()

WEBHOOK_SECRET = os.getenv("COMPOSIO_WEBHOOK_SECRET")


@router.post("/webhooks/composio")
async def composio_webhook(request: Request) -> dict:
    raw = await request.body()

    if WEBHOOK_SECRET:
        try:
            client.triggers.verify_webhook(
                id=request.headers.get("webhook-id", ""),
                payload=raw.decode(),
                secret=WEBHOOK_SECRET,
                signature=request.headers.get("webhook-signature", ""),
                timestamp=request.headers.get("webhook-timestamp", ""),
            )
        except Exception as exc:
            print(f"webhook verification failed: {exc}")
            return {"accepted": False, "reason": "bad signature"}

    event = from_composio_trigger(json.loads(raw or b"{}"))
    accepted = await emit(event)
    return {"accepted": accepted, "event_id": event.id}


@router.post("/events")
async def inject(request: Request) -> dict:
    """Manual injection.

    Deliberately goes through the same normalizer and the same dedup as a real
    webhook, so a demo exercises the real pipeline rather than a fake one
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
