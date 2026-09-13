"""LoopGraph backend — ingestion layer.

    ./run-server.sh

Owns everything up to the normalized Event:

    Gmail / Slack / Drive / Calendar
        -> Composio
        -> FastAPI            (this file + api/events.py)
        -> Event Normalizer   (events/normalizer.py)
        -> Event              (graph/schemas.py)

LangGraph, the Compiler, Verifier, Replanner, Outcome Graph, Supabase and the
UI all sit downstream and are not this layer's concern. Routers for those can
be mounted here as they land.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.events import router as events_router
from app.api.pipeline import router as pipeline_router
from app.events import poller
from app.events.sink import FORWARD_URL, load_seen, seen, set_handler
from app.graph.runtime import runtime
from app.integrations.composio import APPS, USER_ID, active_accounts


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_seen()
    # Build the agents once, then hand every accepted Event straight to LangGraph.
    await runtime.start()
    set_handler(runtime.handle if runtime.ready else None)
    print(f"reasoning pipeline: {'ON' if runtime.ready else 'OFF (no OPENAI_API_KEY)'}")
    print("watching:")
    tasks = poller.start()
    yield
    for task in tasks:
        task.cancel()
    set_handler(None)
    await runtime.stop()


app = FastAPI(title="LoopGraph — Ingestion", lifespan=lifespan)

# The dashboard runs on the Vite dev server, a different origin, so the browser
# preflights every call. Localhost only: this is a demo surface, not a public API,
# and a wildcard would let any page a teammate opens read their loops.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(events_router)
app.include_router(pipeline_router)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "user_id": USER_ID,
        "apps": list(APPS),
        "events_seen": len(seen),
        "pipeline_ready": runtime.ready,
        "persisting": runtime.persisting,
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
