"""LoopGraph backend — ingestion layer.

    cd backend && uvicorn app.main:app --reload --port 8000

Owns everything up to the normalized Event:

    Gmail / Slack / Drive / Calendar
        -> Composio
        -> FastAPI            (this file + api/webhooks.py)
        -> Event Normalizer   (events/normalizer.py)
        -> Event              (graph/schemas.py)

LangGraph, the Compiler, Verifier, Replanner, Outcome Graph, Supabase and the
UI all sit downstream and are not this layer's concern. Routers for those can
be mounted here as they land.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router
from app.events import gmail_poller
from app.events.sink import FORWARD_URL, load_seen, seen
from app.integrations.composio import APPS, USER_ID, active_accounts


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_seen()
    task = asyncio.create_task(gmail_poller.run())
    print(f"gmail poller running every {gmail_poller.POLL_SECONDS:g}s")
    yield
    task.cancel()


app = FastAPI(title="LoopGraph — Ingestion", lifespan=lifespan)
app.include_router(webhooks_router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "user_id": USER_ID,
        "apps": list(APPS),
        "events_seen": len(seen),
        "forwarding_to": FORWARD_URL,
    }


@app.get("/connections")
async def connections() -> dict:
    """Which apps are connected right now, straight from Composio."""
    found = active_accounts()
    return {
        "connections": {
            slug: found.get(slug, {"status": "NOT_CONNECTED"}) for slug in APPS
        },
        "active": sum(1 for s in APPS if found.get(s, {}).get("status") == "ACTIVE"),
        "total": len(APPS),
    }
