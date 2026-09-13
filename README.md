# LoopGraph

LoopGraph turns unfinished objectives into outcome graphs, verifies evidence,
and repairs the plan when circumstances change.

This checkout implements **Teammate A — Outcome Intelligence**, milestones A1–A5:
shared contracts, graph validation, the structured reasoning boundary, and the
Outcome Compiler, Evidence Verifier, and Replanner. Evaluation/integration is next. App integrations,
runtime, and UI belong to the other owners.

See the [implementation plan](docs/LoopGraph_07_Outcome_Intelligence_Implementation_Plan.md)
for ordered tasks, acceptance checks, ownership boundaries, and integration questions.
The [project documents](docs/) define the MVP and stack.

## Local development

The Git/project root is now `LoopGraph/Perceptron`. Run from **Perceptron** with uv
installed (from the old workspace root, first run `cd Perceptron`):

```powershell
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Python is pinned to 3.12; uv manages `Perceptron/.venv`. Select
`Perceptron\.venv\Scripts\python.exe` as your IDE interpreter when the IDE is open
at the old workspace root, or `.venv\Scripts\python.exe` when open at Perceptron.
Manual activation from Perceptron is optional:

```powershell
.\.venv\Scripts\Activate.ps1
```

To include the runtime and integration packages from the stack reference:

```powershell
uv sync --locked --all-extras
```

Core dependencies include FastAPI, LangGraph v1, LangChain/OpenAI, and Pydantic v2.
The optional extras contain Supabase, APScheduler, and Composio. Package versions
are resolved in `uv.lock`. Do not use pip or commit `.venv` or API keys.

Tests run offline without `.env`, using fake providers and the actual OpenAI SDK
with a mock HTTP transport. Never paste credentials into fixture files.

## Structured reasoning (A2)

`Settings.from_env()` reads explicit model, timezone, timeout, output-token budget,
and optional credentials. `.env` is read only when an `env_file` path is supplied;
process environment values take precedence. Credentials are omitted from settings
serialization and representation. The model remains `gpt-5.4-mini`, with medium
effort for compile/replan and low effort for verify.
`tzdata` is a core dependency so IANA timezones work on Windows even without the
optional scheduler packages. The SDK's `httpx2` mock transport is declared in dev
dependencies; `httpx` remains in the specified application stack.

`ReasoningBoundary.run()` accepts a versioned trusted prompt, a Pydantic request,
a closed provider output type, a domain converter, and a business validator that
raises `ValueError` on failure. It returns the validated domain value plus attempt
metadata. The provider, clock, and ID factory are injectable. One invalid result
can be regenerated with validation feedback; a second raises `LLM_OUTPUT_INVALID`.
Refusal, incomplete response, timeout, authentication/access, HTTP, and transport
errors have distinct codes and do not receive the validation retry.

Source content is serialized into a user data envelope with explicit reference
time/timezone. Trusted instructions are versioned separately. No integration tools
are passed to the model. This separation is tested; semantic prompt-injection
resistance still needs the later live evaluation suite.

Provider DTOs use closed key/value structures for JSON maps. The compiler mapper
allocates IDs and timestamps locally and derives initial states and dependency
edges. IDs stay stable during one validation-repair call; cross-call replay
protection still belongs to C. Verifier mapping checks references and shape;
required-field semantics belong to A4. Replan mapping is shape conversion only:
A5 must validate full graph state and operation payloads before any application.

To run the **optional billable live smoke check**, create a local `.env` from
`.env.example`, set your API key there, and explicitly opt in:

```powershell
uv run python -m app.agents.smoke --live --env-file .env
```

The command uses only synthetic missing-document evidence. It validates a
`VerifyDraft`, reports the returned model, prompt/boundary versions, latency,
token counts, and validation result, and closes the client. It is not a workflow
demo or a live compiler-quality evaluation. It has not been run with live
credentials during A2. Run without `--live` to confirm that it refuses to call
the API. For requests failing before a response, attempt metadata contains the
requested model and no token counts.

See the [SQL review](docs/LoopGraph_08_Schema_Review.md) and
[proposed SQL constraints](supabase/review/proposed_contract_constraints.sql)
for the Teammate C handoff. The original `supabase/schema.sql` remains unchanged.

## Outcome Compiler (A3)

`OutcomeCompiler.compile(request)` returns the shared `CompiledGraph` DTO.
`compile_with_metadata(request)` additionally returns per-call prompt versions,
latency, token usage, and validation-attempt metadata. Both use the same versioned
`compiler-v1` prompt for all obligations. No database or connected app is called.

```python
from app.agents.compiler import OutcomeCompiler
from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.config import Settings
from app.graph.schemas import CompileGoalRequest


async def compile_goal(request: CompileGoalRequest):
    settings = Settings.from_env()
    async with OpenAIProvider(settings) as provider:
        compiler = OutcomeCompiler(ReasoningBoundary(provider, settings))
        return await compiler.compile(request)
```

For a live manual check using a synthetic fixture, set credentials in a local
`.env` and explicitly opt in. Run this from Perceptron:

```powershell
uv run python -m app.agents.compile_goal --live --env-file .env --request backend/tests/fixtures/refund_goal.json --reference-time 2026-09-13T10:00:00-04:00
```

Use `promise_goal.json` or `renewal_goal.json` to inspect the other scenarios.
The CLI prints the graph and diagnostics; it does not save or execute the graph.
`--reference-time` supports reproducible replay; omit it for current-time intake.
Without `--live`, the command makes no model request.

If clarification is needed, the graph and all nodes are BLOCKED and no actions
are returned. Runtime must persist the question/assumptions and wait for user input.
Linked events must use reconciliation, not this new-goal compiler. Initial action
parameter conventions, relative-deadline metadata, validation limits, and the
runtime handoff are documented in [A3 handoff](docs/LoopGraph_09_Compiler_Handoff.md).

Offline tests cover scripted outputs for all three scenarios, paraphrases, a new
manual obligation, unsafe proposals, source routing, clarification, and SDK retry.
These tests do not measure live semantic accuracy. No live compilation has been
performed during A3; the paraphrases and manual review criteria are available in
`backend/tests/fixtures/compiler_cases.json` for later evaluation.

## A4 Evidence Verifier

`EvidenceVerifier.verify(VerifyEventRequest)` returns validated evidence decisions
using the A2 boundary and `verifier-v1` prompt. It checks required fields, source
identity, inspected document content, policy dates, and source citations. It
does not mutate graph state or execute actions.

All requirements for each candidate node must be supplied and satisfied by the
current event before the node-level evidence flag can be true. Ambiguous
`must_all_match=false` input is rejected pending a field policy with C. Document
proof requires adapter-supplied inspected text; API-state proof requires a
read-after-write observation.

See the [A4 handoff](docs/LoopGraph_10_Verifier_Handoff.md) for the callable example,
adapter conventions, error handling, and runtime responsibilities. A4 tests are
offline and use scripted model responses, including real SDK parsing over mocked
HTTP. Live semantic quality has not been evaluated. The earlier `agents.smoke`
CLI tests the A2 boundary only; it does not exercise the A4 service.

## A5 Replanner

`Replanner.replan(request, context=context)` returns repair operations against the
existing graph. `ReplanContext` supplies full requirements/actions/evidence and a
state revision alongside the unchanged shared `ReplanRequest`.

Repairs are simulated atomically on a copy: deadline changes cancel stale work,
owner replacement rewires dependencies, and explicit changed evidence permits
requirement edits. Verified history remains intact; external Calendar cleanup is
a new approval-gated proposal. `preview_repair` exposes the validated in-memory
candidate for review. It does not persist changes or execute actions.

See the [A5 handoff](docs/LoopGraph_11_Replanner_Handoff.md) for a callable example,
all operation conventions, replay checks, and the later runtime integration steps.
C's adapter/transaction work is deferred; it does not block this implementation.
The full suite has 295 offline tests, including 58 A5 tests. Live semantic and
connected-app evaluation remain A6 work.

## Layout

```text
pyproject.toml             Root Python project and dependency configuration
uv.lock                   Reproducible dependency resolution
backend/app/constants.py  Canonical wire enums
backend/app/graph/         Shared models and initial-graph validation
backend/app/agents/        Compiler, verifier, replanner, structured provider, DTOs
backend/app/prompts/       Versioned boundary and intelligence instructions
backend/app/config.py     Explicit environment configuration
backend/tests/            Offline contract and graph validation tests
supabase/review/           SQL proposals for teammate review (not migrations)
supabase/tests/            Disposable-database review/regression SQL scripts
docs/                     Product specifications and working implementation plan
```

The installed Python package is `app`, matching the architecture's `backend/app`.
Tests use complete, hand-authored expected graphs. These fixtures demonstrate
contract compatibility; they do not demonstrate live LLM compilation or semantic
verification quality.
