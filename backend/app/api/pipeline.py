"""What the reasoning pipeline did with each ingested event.

    GET /pipeline          recent decisions, newest first
    GET /loops             loops currently being tracked
    GET /loops/{loop_id}   one loop's full graph
"""

from fastapi import APIRouter

from app.graph.runtime import USER_ID, LiveLoopRepository, runtime

router = APIRouter()


@router.get("/pipeline")
async def pipeline(limit: int = 20) -> dict:
    items = list(runtime.state.results)[-limit:]
    items.reverse()
    return {"ready": runtime.ready, "count": len(items), "results": items}


@router.get("/loops")
async def loops() -> dict:
    # Built from the same LiveState the router reads, so this endpoint shows exactly
    # the candidate set routing will consider - not a parallel view that can drift.
    summaries = await LiveLoopRepository(runtime.state).list_routable_loops(USER_ID)
    return {
        "count": len(runtime.state.graphs),
        "routable": [
            {
                "loop_id": s.loop_id,
                "title": s.title,
                "goal": s.goal,
                "status": s.status,
                "open_nodes": s.open_node_titles,
                "people": s.people,
                "identifiers": s.identifiers,
                "thread_ids": s.thread_ids,
            }
            for s in summaries
        ],
    }


@router.get("/loops/{loop_id}")
async def loop_detail(loop_id: str) -> dict:
    rows = runtime.state.graphs.get(loop_id)
    if rows is None:
        return {"found": False, "loop_id": loop_id}
    return {"found": True, **rows}
