# LoopGraph

**Unfinished business, tracked to completion.**

A refund you were promised. A deck a colleague said they'd send. A policy renewal
that needs a document by the 30th. These obligations live scattered across Gmail,
Slack, Drive and Calendar, and they close only when something *proves* they closed
— a confirmation email, an uploaded receipt, a signed PDF.

LoopGraph watches those four apps, turns each unfinished objective into an
**outcome graph**, routes every incoming event to the goals it affects, verifies
whether the event is real evidence, and repairs the plan when circumstances change.

**Demo link**: ![https://drive.google.com/file/d/1wWfdi2hOnweMlXci3PlG9OzkgDvlOGrD/view?usp=sharing]

---

## The pipeline

```
  Gmail · Slack · Drive · Calendar
            │
            ▼   Composio (polled, 10s per app)
      ┌───────────────┐
      │  Normalizer   │  raw payload ──▶ validated Event
      └───────┬───────┘
              ▼
      ┌───────────────┐
      │     Sink      │  dedup, log, hand off
      └───────┬───────┘
              ▼
  ══════════ LangGraph event workflow ══════════
      ingest ─▶ route ─▶ verify ─▶ replan ─▶ finalize
                  └────▶ compile ──────────────┘
  ══════════════════════════════════════════════
              │
              ▼
        Postgres  ·  or an in-memory recorder
```

Each stage sits behind a protocol, so the whole thing runs end to end with no
database, no API key and no network — see [run_demo.py](run_demo.py).

| Stage | What it decides | Where |
|---|---|---|
| **ingest** | Have we seen this exact occurrence before? | [workflow.py](backend/app/graph/workflow.py) |
| **route** | Which existing loop(s) does this event affect? | [events/router.py](backend/app/events/router.py) |
| **verify** | Does this event actually *prove* anything? | [agents/verifier.py](backend/app/agents/verifier.py) |
| **compile** | No loop fits — is this a new obligation? | [agents/compiler.py](backend/app/agents/compiler.py) |
| **replan** | The world changed — repair the graph | [agents/replanner.py](backend/app/agents/replanner.py) |
| **finalize** | Mark processed, last, so a crash is replayable | [workflow.py](backend/app/graph/workflow.py) |

---

## The Event Router

The one component that touches every single event, so it is staged
cheapest-first. Most events never reach a model at all.

| Stage | Signal | Confidence | Cost |
|---|---|---|---|
| **0** | Event arrived already linked upstream | `1.00` | nothing |
| **1a** | Thread id already linked to a loop | `0.99` | one indexed read |
| **1b** | Order/policy/invoice id appears verbatim in the body | `0.97` | regex |
| **1c** | Actor owns an open node in the loop | `0.55` | in-memory |
| — | Amount corroborates a signal that already fired | `+0.02` | in-memory |
| **2/3** | Semantic judgement — *and* "is this a new obligation?" | model | one call |

Anything at or above `0.90` short-circuits and answers immediately. Below `0.50`
is discarded. Two confident candidates within `0.15` of each other are both
returned rather than the router inventing a winner. Every threshold is a named
constant at the top of [router.py](backend/app/events/router.py#L47-L80).

**The router is read-only.** It never writes, never calls Gmail/Slack/Drive/
Calendar, never mutates a loop, and never decides whether evidence *proves*
anything — a match says "this event is about that loop", nothing more. The
Evidence Verifier makes the second call.

Three deliberate refusals worth knowing about:

- An actor match can never short-circuit. Sarah messaging about something
  unrelated to the loop she owns a node in is an ordinary Tuesday.
- An amount never scores on its own. `"129 Main Street"` must not route a $129 refund.
- A model naming a loop it was not offered is a hallucination, not a match — it
  is filtered against the candidate set. A failed model call returns *nothing*;
  it never invents a loop or proposes a new one.

---

## Repository layout

```
backend/app/
  main.py                 FastAPI app: lifespan, /health, /connections
  constants.py            Canonical wire enums (StrEnum, so LoopStatus.ACTIVE == "ACTIVE")
  config.py               Settings.from_env — explicit, no import-time side effects

  api/
    events.py             POST /events (manual injection), GET /events
    pipeline.py           GET /pipeline, GET /loops, GET /loops/{id}

  events/                 ── ingestion + routing ──
    poller.py             One task per app, adaptive backoff when throttled
    classify.py           created / updated / deleted, per app, from the payload
    normalizer.py         The only code that knows what a raw Gmail payload looks like
    sink.py               Dedup, event log, handoff into the reasoning runtime
    identifiers.py        Pure regex: order ids, policy numbers, amounts
    router.py             The Event Router (above)
    router_models.py      LoopSummary, RouteDraft, prompt projections
    router_validation.py  Collect-all-issues validation of a RouteEventResponse
    repository.py         LoopRepository protocol + fixture double
    hydration.py          Row dicts ──▶ VerifyEventRequest
    pipeline.py           Route-then-verify composition, returns a plan, performs nothing

  agents/                 ── outcome intelligence ──
    llm.py                OpenAIProvider + ReasoningBoundary (validate, repair once, fail)
    compiler.py           Goal ──▶ CompiledGraph
    verifier.py           Event ──▶ per-node evidence decisions
    replanner.py          Verification ──▶ atomic graph repair operations
    smoke.py              Opt-in billable live check of the boundary

  graph/
    schemas.py            Shared DTOs. extra="forbid" — unknown keys RAISE
    workflow.py           The LangGraph event workflow
    state.py              PipelineState + RuntimeStore protocol + in-memory recorder
    runtime.py            Live wiring: real model, real agents, memory or Postgres
    postgres_store.py     Durable implementations of the three runtime protocols
    *_validation.py       Compiler / evidence / repair / semantic validation

  integrations/
    composio.py           The only module that talks to Composio
  db/
    db.py                 All SQL lives here. Nobody else writes SQL.
  prompts/                Versioned, trusted instructions (boundary, router, compiler, …)

backend/tests/            Offline suite — fake providers, real SDK over mocked HTTP
supabase/schema.sql       loops, outcome_nodes, edges, events, evidence, actions, …
docs/                     Product spec, contracts, ownership map, per-agent handoffs
scripts/                  connect_apps.py (one-time OAuth), check_apps.py (prove it works)
run_demo.py               Full pipeline, stubbed agents. No key, no DB, no internet.
run_live.py               Full pipeline, real model, in-memory DB. Costs tokens.
```

---

## Getting started

Python **3.12** (pinned in [pyproject.toml](pyproject.toml)).

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

pip install -e ".[runtime,integrations]"
pip install pytest pytest-asyncio ruff
```

Then copy the environment template:

```bash
cp .env.example .env
```

`.env` is gitignored and must stay that way.

### The offline demo — start here

No API key, no database, no internet. The agents are stubs; the routing logic
and graph wiring are real.

```bash
python run_demo.py
```

Six scenarios: a refund that matches on order number alone, the same email
delivered twice, a Slack message that needs judgement, a brand-new obligation, a
newsletter that says "refund" but means nothing, and a late refund for a loop
that already closed.

### The live run — real model, fake database

```bash
python run_live.py --check    # one cheap call, just validates the key
python run_live.py            # 3 scenarios
python run_live.py --all      # all 6
```

Needs `OPENAI_API_KEY` in `.env`. Costs real tokens. Writes go to an in-memory
recorder, so no Postgres required.

### The ingestion service

```bash
python scripts/connect_apps.py          # one-time OAuth for all four apps
python scripts/connect_apps.py --status # what's connected right now
python scripts/check_apps.py            # read one real item from each app

./run-server.sh                         # or: uvicorn app.main:app --reload
./watch-events.sh                       # follow events live, second terminal
```

| Endpoint | Returns |
|---|---|
| `GET /health` | Connected apps, events seen, loops tracked, pipeline on/off |
| `GET /connections` | Live Composio connection status per app |
| `POST /events` | Manual injection — same normalizer, same dedup as a poll |
| `GET /events` | Recent normalized Events, newest first |
| `GET /pipeline` | What the reasoning pipeline decided, per event |
| `GET /loops` | Loops currently routable — the exact candidate set routing sees |
| `GET /loops/{id}` | One loop's full graph |

The pipeline turns itself on only when `OPENAI_API_KEY` is present; without it
ingestion still runs and `/health` reports `pipeline_ready: false`.

### Tests

```bash
cd backend && python -m pytest -q
```

The suite is fully offline: fake providers plus the real OpenAI SDK driven over a
mock HTTP transport. It needs `openai` and `httpx2` installed even though it never
reaches the network. Never put credentials in a fixture.

Lint:

```bash
ruff check .
ruff format --check .
```

---

## Configuration

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Enables the reasoning pipeline. Absent ⇒ ingestion only. |
| `LOOPGRAPH_MODEL` | Default `gpt-5.4-mini` |
| `LOOPGRAPH_TIMEZONE` | IANA zone, default `America/New_York` |
| `LOOPGRAPH_LLM_TIMEOUT_SECONDS` | Default 60 (the router overrides to 15) |
| `LOOPGRAPH_LLM_MAX_OUTPUT_TOKENS` | Default 12000 |
| `COMPOSIO_API_KEY` | Must be a **project** key from platform.composio.dev |
| `USER_ID` | Composio's user — identifies connected accounts |
| `LOOPGRAPH_USER_ID` | The LoopGraph tenant, default `user_001`. Not the same thing. |
| `PGHOST` `PGPORT` `PGUSER` `PGPASSWORD` `PGDATABASE` | Discrete, not a URL, so a `@` in the password needs no escaping |
| `LOOPGRAPH_PERSIST` | `0` forces the in-memory store even with a database available |
| `LOOPGRAPH_WEBHOOK_URL` | Optional outbound POST of every Event |

Persistence turns on automatically when `PGHOST` is set. Swapping between memory
and Postgres changes nothing above the three protocols — `RuntimeStore`,
`LoopRepository`, `LoopGraphSource`.

Poll intervals are per-app and live in
[poller.py](backend/app/events/poller.py#L40-L45), because Slack's rate limit is
an order of magnitude tighter than Google's and one shared number cannot serve both.

---

## House rules

These are enforced by the code, not just documented:

- **Every LLM output validates through Pydantic.** Never parse model prose with regex.
- **All thresholds are named module-level constants.** No inline literals.
- **`now: datetime` is injected.** Nothing in routing logic reads the wall clock.
- **Enums are `StrEnum`**, so `LoopStatus.ACTIVE == "ACTIVE"` is `True`.
- **All DTOs use `extra="forbid"`.** Unknown keys raise.
- **Validation collects every issue into a list and raises once**, with all of them.
- **`app/db/db.py` owns all SQL.** Nobody else writes any.
- **The reasoning layer never imports `app.integrations`**, and vice versa.
- **Prompts are versioned and trusted.** Source content goes in a user-data
  envelope with an explicit reference time; no tools are ever handed to the model.

Some design decisions worth reading the comments for:

- `Event.dedup_key` folds in the item's own version stamp, so an *edited* message
  is new work while a re-delivered webhook is not
  ([schemas.py](backend/app/graph/schemas.py#L191-L206)).
- `finalize` marks the event processed **last**, so a crash mid-pipeline leaves it
  replayable rather than silently consumed.
- Every verifier decision is persisted, including `UNRELATED` and `INSUFFICIENT`.
  The assessment is the audit record, not just the favourable half of it.
- All four apps are **polled**, not webhooked — nothing needs a public URL, so
  there is no tunnel to die mid-demo.

---

## Ownership

Four-person project with strict module boundaries
([doc 04](docs/LoopGraph_04_Architecture_Team_Ownership_Map.md)):

| | Scope |
|---|---|
| **A** | Outcome Intelligence — `agents/`, `config.py`, graph validation |
| **B** | Integrations — `integrations/`, connectors, action execution |
| **C** | Runtime, reliability, persistence — `db/`, schema, shared contracts |
| **D** | Frontend, product, demo |
| **This checkout's author** | The Event Router — `events/`, `tests/` |

`graph/schemas.py` and `constants.py` are shared contracts. Changes to them get
coordinated, never made unilaterally.

---

## Documentation

| Document | Contents |
|---|---|
| [01 — Product & MVP Scope](docs/LoopGraph_01_Product_MVP_Scope.md) | What ships and what does not |
| [02 — Use Case Specifications](docs/LoopGraph_02_Use_Case_Specifications.md) | The refund, promise and renewal scenarios |
| [03 — Data Models & API Contracts](docs/LoopGraph_03_Data_Models_API_Contracts.md) | Every DTO and endpoint |
| [04 — Architecture & Ownership](docs/LoopGraph_04_Architecture_Team_Ownership_Map.md) | Module boundaries |
| [05 — Reliability & Test Matrix](docs/LoopGraph_05_Reliability_Test_Matrix.md) | Failure modes and their tests |
| [06 — Demo Script](docs/LoopGraph_06_Demo_Script_Judging_Story.md) | The judging narrative |
| [07 — Outcome Intelligence Plan](docs/LoopGraph_07_Outcome_Intelligence_Implementation_Plan.md) | A's ordered milestones |
| [08 — Schema Review](docs/LoopGraph_08_Schema_Review.md) | SQL findings for C |
| [09](docs/LoopGraph_09_Compiler_Handoff.md) · [10](docs/LoopGraph_10_Verifier_Handoff.md) · [11](docs/LoopGraph_11_Replanner_Handoff.md) | Compiler, Verifier and Replanner handoffs |

---

## Known gaps

Stated plainly rather than discovered later:

- **`Event` has no `user_id`.** Tenancy is passed explicitly into the router,
  pipeline and workflow instead of being inferred. This is a gap in the shared
  contract, kept visible on purpose.
- **Drive file *contents* are not read.** An Event carries a file's name, link and
  category — enough to say a receipt was uploaded, not enough to say it is for
  $129. The working implementation is commented out in
  [composio.py](backend/app/integrations/composio.py#L282-L329) with its costs.
- **Live semantic quality is unevaluated.** The suite is offline and proves
  contract compatibility and control flow, not that the model reasons well.
- **The live runtime holds state in memory** unless `PGHOST` is set. State still
  *accumulates* — a loop compiled from one email becomes a routing candidate for
  the next — which is the property the demo depends on.
