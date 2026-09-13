"""Loop endpoints — what the UI reads once the runtime has built a graph.

    GET /loops            every loop for the user, newest first
    GET /loops/{id}       one loop with nodes, edges, evidence, actions, activity

Both return 503 rather than 500 when the database is not configured, because that
is a deployment state the UI should render as "the runtime is off", not an error.
"""

from fastapi import APIRouter, HTTPException, Request

from app.db import db, queries
from app.integrations.composio import USER_ID

router = APIRouter()


def _require_runtime(request: Request) -> None:
    if getattr(request.app.state, "runtime", None) is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "runtime not configured",
                "problems": getattr(request.app.state, "runtime_problems", []),
            },
        )


@router.get("/loops")
async def list_loops(request: Request) -> dict:
    """Every loop for this user, with node counts for the list view."""
    _require_runtime(request)
    loops = await queries.list_loops_for_user(USER_ID)
    return {"count": len(loops), "loops": loops}


@router.get("/loops/{loop_id}")
async def get_loop(loop_id: str, request: Request) -> dict:
    """One loop in full.

    Tenancy is re-checked here rather than trusted from the path: `get_loop_detail`
    takes only a loop id, so without this a guessed id would return another user's
    graph.
    """
    _require_runtime(request)

    owned = await queries.load_loop_graph(USER_ID, loop_id)
    if owned is None:
        raise HTTPException(status_code=404, detail="loop not found")

    detail = await db.get_loop_detail(loop_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="loop not found")

    # get_loop_detail returns evidence but not the requirements that evidence is
    # judged against, so a caller cannot show why a node is still unproven. The
    # tenancy read above already loaded them; pass them through rather than make
    # the UI issue a second request for rows we are holding.
    detail["requirements"] = owned["requirements"]
    return detail
