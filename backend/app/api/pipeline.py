"""What the reasoning pipeline did with each ingested event.

    GET /pipeline          recent decisions, newest first
    GET /loops             loops currently being tracked
    GET /loops/{loop_id}   one loop's full graph
"""

from fastapi import APIRouter

from app.graph.runtime import USER_ID, runtime

router = APIRouter()


@router.get("/pipeline")
async def pipeline(limit: int = 20) -> dict:
    items = list(runtime.state.results)[-limit:]
    items.reverse()
    return {"ready": runtime.ready, "count": len(items), "results": items}


@router.get("/loops")
async def loops() -> dict:
    # Read through runtime.repo, the very object the router uses. Querying LiveState
    # directly would show an empty list whenever persistence is on, because the pipeline
    # would be writing to Postgres while this endpoint read memory.
    summaries = await runtime.repo.list_routable_loops(USER_ID)
    return {
        "count": len(summaries),
        "persisted": runtime.persisting,
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
    rows = await runtime.graphs.load_loop_graph(USER_ID, loop_id)
    if rows is None:
        return {"found": False, "loop_id": loop_id}
    return {"found": True, **rows}
