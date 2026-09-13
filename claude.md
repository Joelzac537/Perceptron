# LoopGraph

Four-person hackathon project. Python 3.12, FastAPI, Pydantic v2. Strict module boundaries.

## My scope this session
I own ONLY `backend/app/events/` and `backend/tests/`.

## Files you must NOT edit
- `backend/app/graph/schemas.py` — shared contract owned by another teammate.
  If it needs a change, STOP and tell me. Do not edit it.
- `backend/app/constants.py` — same.
- `backend/app/config.py`, `backend/app/graph/semantic_validation.py` — owned by A.
- `backend/app/agents/`, `backend/app/integrations/`, `frontend/` — not mine.

## Component I am building: the Event Router
Given one normalized Event, answer two questions:
  1. Which existing loop(s) does it affect?
  2. If none, is it a new obligation that should become a loop?

## I never want you to perform any commits, if there is a need for commit, let me know I will do it personally. 

The router READS from the database through an injected LoopRepository. It never
WRITES anything, never calls Gmail/Slack/Drive/Calendar, never mutates a loop or
node, and never decides whether evidence proves anything.

## House rules
- Every LLM output validates through Pydantic. Never parse model prose with regex.
- All thresholds are named module-level constants, never inline literals.
- Inject `now: datetime` as a parameter; never call `datetime.now()` inside logic.
- Enums in app/constants.py are StrEnum, so `LoopStatus.ACTIVE == "ACTIVE"` is True.
- All DTOs in schemas.py use `extra="forbid"`. Unknown keys RAISE.
- Validation style: collect all issues into a list, raise once with all of them.

## Workflow
- Run `cd backend && python -m pytest -q` after every change and show me the output.
- Do not move on to the next file until the current one's tests pass.