"""What the reasoning layer is and what it has done.

    GET /runtime      the four agents, whether each is configured, and its counts

The UI needs a way to show the reasoning layer as something other than a black
box. Every number here is read from rows the agents actually wrote — evidence for
the verifier, activity logs for the replanner, loops for the compiler — rather
than from instrumentation that could drift away from the truth.
"""

from fastapi import APIRouter, Request

from app.config import Settings
from app.db import queries
from app.integrations.composio import USER_ID

router = APIRouter()

# Ordered as an event flows through them, because that is how the UI reads.
AGENTS = [
    {
        "key": "router",
        "name": "Event Router",
        "question": "Which existing loops does this event affect?",
        "module": "app/events/router.py",
        "detail": "Staged cheapest-first: pre-linked, then thread and identifier "
        "matching, and only then a model call.",
    },
    {
        "key": "compiler",
        "name": "Outcome Compiler",
        "question": "Is this a new obligation, and what has to be true to close it?",
        "module": "app/agents/compiler.py",
        "detail": "Only ever sees events that matched nothing. An event belonging to "
        "a tracked goal is evidence about it, not a new goal.",
    },
    {
        "key": "verifier",
        "name": "Evidence Verifier",
        "question": "Does this event prove the outcome actually happened?",
        "module": "app/agents/verifier.py",
        "detail": "Runs once per matched loop. A match says the event is about the "
        "loop; only this says whether it satisfies it.",
    },
    {
        "key": "replanner",
        "name": "Replanner",
        "question": "The plan broke — how should the graph change?",
        "module": "app/agents/replanner.py",
        "detail": "Repairs apply atomically, and each operation writes its reason to "
        "the activity log so the change can be explained.",
    },
]


def _stat_line(key: str, stats: dict[str, int]) -> list[dict]:
    """The counts worth showing beside each agent, in reading order."""
    if key == "router":
        return [
            {"label": "events seen", "value": stats["events_total"]},
            {"label": "routed", "value": stats["events_processed"]},
            {"label": "linked to a loop", "value": stats["events_linked"]},
        ]
    if key == "compiler":
        return [
            {"label": "loops built", "value": stats["loops_total"]},
            {"label": "outcomes", "value": stats["nodes_total"]},
            {"label": "need clarifying", "value": stats["clarifications"]},
        ]
    if key == "verifier":
        return [
            {"label": "evidence written", "value": stats["evidence_total"]},
            {"label": "proving", "value": stats["evidence_proving"]},
            {"label": "outcomes verified", "value": stats["nodes_verified"]},
        ]
    return [
        {"label": "repairs applied", "value": stats["repairs"]},
        {"label": "actions proposed", "value": stats["actions_total"]},
        {"label": "loops completed", "value": stats["loops_completed"]},
    ]


@router.get("/runtime")
async def runtime_status(request: Request) -> dict:
    ready = getattr(request.app.state, "runtime", None) is not None
    problems = getattr(request.app.state, "runtime_problems", [])

    # Settings never reads a file on import, so this is cheap and always current.
    settings = Settings.from_env()

    stats: dict[str, int] = {}
    if ready:
        stats = await queries.runtime_stats(USER_ID)

    return {
        "ready": ready,
        "problems": problems,
        "model": settings.model,
        "timezone": settings.timezone,
        "stats": stats,
        "agents": [
            {**agent, "stats": _stat_line(agent["key"], stats) if ready else []}
            for agent in AGENTS
        ],
    }
