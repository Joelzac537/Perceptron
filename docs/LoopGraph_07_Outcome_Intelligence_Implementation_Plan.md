# LoopGraph — Teammate A Implementation Plan

Started: 2026-09-13. Scope: Outcome Intelligence. This is the working checklist;
mark a step complete only after its acceptance checks pass.

## Product understanding

LoopGraph maintains an evidence-backed graph of a user's unfinished objective.
The shared lifecycle is compile → act → observe → verify → complete, with repair
when evidence, deadlines, ownership, or requirements change. Sending a message,
creating a reminder, or saving a file does not by itself achieve the root goal.

The three MVP scenarios exercise the same graph and reasoning contracts:

| Scenario | Final evidence | Main repair | Critical negative case |
|---|---|---|---|
| Return/refund | Merchant confirms the correct order, amount, and currency were refunded (MVP proxy) | Move refund deadline and cancel stale escalation | Wrong order or a sent follow-up cannot complete refund |
| Promised document | Correct final artifact is received | Supersede Sarah's responsibility with Mike's | Draft file cannot satisfy final-file requirement |
| Paperwork/renewal | Valid current proof is submitted and landlord acknowledges | Replace full-policy requirement with declaration page | Renewal confirmation without a PDF cannot unlock submission |

Sources: [scope](LoopGraph_01_Product_MVP_Scope.md),
[use cases](LoopGraph_02_Use_Case_Specifications.md),
[contracts](LoopGraph_03_Data_Models_API_Contracts.md),
[ownership](LoopGraph_04_Architecture_Team_Ownership_Map.md),
[reliability](LoopGraph_05_Reliability_Test_Matrix.md),
[demo](LoopGraph_06_Demo_Script_Judging_Story.md), and
[stack reference](LoopGraph_Tech_Stack_Reference.docx).

## Ownership and stack

A owns compiler, verifier, replanner, prompts, structured model output, and graph
semantic validation. A contributes the initial shared schemas for C to integrate.
B owns Composio/app reads and writes; C owns LangGraph orchestration, API routes,
database, scheduling, approval enforcement, and applying graph operations; D owns
the UI. Intelligence returns proposals and never writes to apps or the database.

Use Python 3.12, Pydantic v2, GPT-5.4 Mini, LangGraph v1, LangChain/OpenAI SDK,
FastAPI, Supabase/Postgres, Composio, and APScheduler from the supplied stack.
Frontend remains Next.js/TypeScript, Tailwind/shadcn, and React Flow; deployment
remains Vercel/Railway under the respective owners.

One root `pyproject.toml`, `uv.lock`, and `.venv` support commands run from
LoopGraph. Python code stays in `backend/app/` as specified by the architecture.
Root packaging is a deliberate layout adaptation to the requested root environment.
Runtime and integration dependencies are optional extras so A's unit tests can run
without external-service configuration. `uv sync --all-extras` installs the full
backend dependency set. The frontend is deferred to D.

## Decisions and integration questions

1. **Wire vocabulary:** the detailed contract document governs uppercase enums,
   `source_app`, `event_type`, and `DEADLINE_REACHED`. The DOCX's lowercase schema
   and `DEADLINE_MISSED` snippet are illustrative, not additional states.
2. **Dependency direction:** a DEPENDS_ON edge goes from dependent to prerequisite.
   It must agree with the node's `depends_on` array. Root dependencies must reach
   the whole initial graph, and the dependency graph must be acyclic.
3. **Evidence:** `Evidence.verified` means the evidence assessment was performed;
   an INSUFFICIENT assessment can have `verified=true` (contract section 9.3).
   It cannot complete a node. Completion requires PROVES, satisfied requirements,
   satisfied dependencies, verified required actions, and no contradictions.
4. **Refund scope:** merchant refund confirmation is the documented MVP proxy;
   never claim bank settlement was independently observed.
5. **Approval:** sending email/Slack and cancelling external Calendar events require
   approval. The DOCX also lists external Calendar updates as medium risk; use
   that policy for proposals and flag it for B/C's integration review. Cancelling
   an unexecuted internal action is distinct from deleting an external event.
6. **Time:** reject naive timestamps. Compiler adapter must supply an explicit
   reference time and timezone for relative dates. Five business days cannot be
   converted to a fixed date until merchant receipt is known. Initial convention
   is weekdays in the user's timezone; holiday handling needs explicit policy.
7. **Open replan context gap:** `ReplanRequest` currently has node/action IDs but
   no full actions or evidence requirements. A2/A5 must agree with C on an additive
   context object or service argument before validating action cancellation and
   requirement edits. A2 preserves the shared wire shape. Its replan mapper only
   converts shape; it does not authorize or validate repair application. A5 still
   requires a full-state context agreement with C.
8. **Provider schema gap (resolved in A2):** domain contracts contain open dictionaries. Strict
   Structured Outputs requires closed object schemas. A2 must introduce typed
   provider-facing DTOs (including key/value lists where needed) and map them to
   shared models. A2 implements closed recursive tagged JSON values, typed
   compile/verify/replan drafts, and explicit mapping. Domain schemas are rejected
   at the provider boundary; original shared DTOs remain unchanged.
9. **Identity and idempotency:** runtime remains authoritative for event dedupe,
   persisted IDs, and action replay prevention. A validates duplicate references
   and proposal keys; provider DTOs should use temporary IDs mapped at the boundary.
10. **Clarification:** missing essential identity/deadline details must be surfaced
    through assumptions/clarification, with no executable proposal based on guessed
    recipients or document contents. A1 allows a valid draft graph; A3 must define
    the runtime's treatment of clarification before returning actions.

The schema bootstrap adds validation of existing documented constraints without
renaming fields. Flexible app/action/evidence-type strings stay flexible. Graph
operation names are constrained to the documented vocabulary, while operation
payload validation is deferred to A5. No team notifications have been sent.

## Ordered implementation milestones

### A1 — Environment and shared contract foundation

- [x] Create root Python 3.12 `.venv` with uv, dependency manifest, lockfile,
      `.python-version`, `.env.example`, and development commands.
- [x] Implement shared domain models and compile/verify/replan DTOs; preserve wire
      names, forbid unknown fields, require timezone-aware timestamps, bound
      confidence, use independent collection defaults, and validate goal intake.
- [x] Implement pure initial-graph validation: IDs, loop ownership, root, edges,
      dependencies, cycles, reachability, evidence/action references, initial
      states, available apps, and approval-safe proposals.
- [x] Add complete hand-authored graph fixtures for all three workflows and
      contract examples; label them as expected outputs, not compiled AI results.
- [x] Run offline pytest, Ruff lint/format, and dependency consistency checks.

Acceptance: all three expected graphs validate; broken references/cycles and
unsafe proposals are rejected; setup is reproducible with `uv sync --locked`.
This establishes interfaces, not a working natural-language compiler.

### A2 — Structured reasoning boundary

Depends on A1. Files: `backend/app/agents/llm.py`, `config.py`,
`backend/app/agents/provider_schemas.py`, and `backend/app/prompts/`.

- [x] Add explicit model/timezone configuration and an injectable async provider.
- [x] Implement closed provider DTOs and tested domain mapping; preserve
      `gpt-5.4-mini` with medium effort for compile/replan and low for verify.
- [x] Use the OpenAI SDK's structured parsing; no regex or prose-to-state parsing.
- [x] Add one bounded repair attempt with validation errors. A second invalid
      result raises `LLM_OUTPUT_INVALID`. Separate timeout, refusal, incomplete
      generation, credentials, and transport failures from invalid output.
- [x] Inject fake provider, clock, and ID generator for deterministic unit tests.
- [x] Treat source messages/attachment text as untrusted data, never instructions;
      do not expose integration tools to the reasoning model.
- [x] Test valid structured output, invalid twice, refusal, timeout, and unknown
      references. Inspect generated provider schemas for unsupported constructs.

Acceptance: provider contract tests pass without credentials; live smoke test is
opt-in and reports the actual model, prompt version, latency, and validation result.

Implementation: `Settings`, `OpenAIProvider`, `ReasoningBoundary`, closed draft
DTOs, trusted `boundary-v1` prompt, and domain mappers. The installed OpenAI 3.13
SDK's raw-response wrapper allows status inspection before its synchronous
`parse()`; this prevents incomplete JSON from being mistaken for invalid model
output. Transport calls remain async. No SDK transport retries are enabled.

The compiler draft uses temporary node references and locally generated IDs,
timestamps, initial states, and dependency edges. Action keys remain stable only
within one boundary call; persisted deduplication is C's responsibility. Source
messages stay in the data envelope, including on repair. Later semantic evaluation
must test whether the model actually resists adversarial source instructions.

The opt-in smoke CLI uses synthetic missing-document evidence and the real
`VerifyDraft` schema. Offline tests exercise the actual SDK over mock HTTP.
No live API evaluation was run; production compiler/verifier/replanner prompts
and domain business checks beyond A1 remain in A3–A5.

### A3 — Outcome Compiler

Depends on A2. Files: `agents/compiler.py`, `prompts/compiler.md`.

- [ ] Implement `async OutcomeCompiler.compile(CompileGoalRequest) -> CompiledGraph`.
- [ ] Build one generic prompt for outcomes, prerequisites, evidence, actors,
      deadlines, recovery strategies, and proposed actions across all scenarios.
- [ ] Make missing facts explicit; avoid guessed actor addresses and dates.
- [ ] Map provider output to stable domain objects and run A1 validation.
- [ ] Validate user/source identity and available-app limits at the boundary.
- [ ] Add scenario tests for graph expectations and paraphrases, including an
      unseen obligation to detect scenario-specific branching.

Acceptance: RR-01, PR-01, C-01/02/03 and renewal compilation; tests distinguish
fixture contract checks from measured live model quality. A → C handoff #1:
runtime can call the compiler with an injected provider and consume its DTO.

### A4 — Evidence Verifier

Depends on A2/A3. Files: `agents/verifier.py`, `prompts/verifier.md`,
`graph/evidence_validation.py`.

- [ ] Implement `async EvidenceVerifier.verify(VerifyEventRequest) -> VerifyEventResponse`.
- [ ] Support all six evidence relationships with reasons and extracted fields.
- [ ] Reject unknown/duplicate decision node IDs and mismatched loop context.
- [ ] Deterministically check required identity, amount/currency, source app,
      document version/validity, and missing fields after semantic extraction.
- [ ] Define `must_all_match=false` and multiple-requirement aggregation explicitly
      with C; never weaken identity constraints through optional matching.
- [ ] Reject unsupported claims based only on filenames or uninspected attachments.
- [ ] Keep evidence satisfaction separate from permission to transition a node;
      C enforces dependencies, contradictions, and required action verification.
- [ ] Test correct/wrong refund, draft/final artifact, expired/current policy,
      confirmation without PDF, submission without attachment, acknowledgement,
      unrelated events, and injected instructions in source content.

Acceptance: V-01/02, RR-05/06, PR-08, PW-02/03, LLM-04. High confidence never
overrides a required-field mismatch. No node state is mutated by the verifier.

### A5 — Replanner and repair validation

Depends on A3/A4 and resolution of context gap #7. Files:
`agents/replanner.py`, `prompts/replanner.md`, `graph/repair_validation.py`.

- [ ] Implement `async Replanner.replan(ReplanRequest) -> ReplanResponse` with
      additive full-state context agreed with C.
- [ ] Type and validate each operation payload and require human-readable reasons.
- [ ] Support deadline changes, owner reassignment, new/superseded nodes,
      evidence requirement changes, and obsolete pending-action cancellation.
- [ ] Validate changes on a copy of the graph; reject the entire proposal if any
      reference, new cycle, state change, or action cancellation is invalid.
- [ ] Preserve unaffected IDs, completed evidence, and history. Owner replacement
      rewires dependencies and removes the old dependency edge consistently.
- [ ] Keep root evidence criteria intact unless an explicit changed requirement
      justifies repair; never use VERIFY_NODE to bypass verifier checks.
- [ ] Prevent duplicate replacement nodes/actions when the same repair is proposed
      again. C still applies operations transactionally and enforces idempotency.
- [ ] Treat already VERIFIED actions as history; external reminder cancellation
      is a new approval-gated action, not a rewrite of verified execution history.

Acceptance: R-01/02, RR-07/08, PR-06, ID-03, RC-01 through RC-04. A → C handoff #2:
runtime consumes validated operations and records one coherent repair activity.

### A6 — Evaluation and team integration

Depends on A3–A5; live app/runtime checks require B/C/D components.

- [ ] Expand shared Event fixtures to the complete sequences listed in test matrix
      section 7; include contradictory and out-of-order evidence.
- [ ] Add opt-in live semantic evaluations with expected decisions/invariants,
      prompt versions, actual outcomes, latency, and usage; never assert exact prose.
- [ ] Exercise primary refund sequence and secondary promise/renewal sequences
      through C's event pipeline. Manual injection uses the same pipeline.
- [ ] Verify approval, read-after-write, dedupe, restart persistence, stale-action
      cleanup, and UI graph updates with the owning teammates.
- [ ] Record measured results against the original reliability IDs; label mocked
      and live tests separately. Provide integration examples and known limits.

Acceptance: Teammate A's definition of done in ownership section 4.7 is satisfied;
the team separately owns the full E2E-RR-01/E2E-PR-01/E2E-PW-01 demo acceptance.

## Working rhythm

Finish one milestone, run its checks, update this checklist, and leave the next
step explicit. Prioritize the refund path, then owner reassignment, then changed
document requirements. Do not add new infrastructure, vendor routing, app
integrations, or a scenario-specific workflow engine.

## Technical references checked during setup

- [uv project layout](https://docs.astral.sh/uv/concepts/projects/layout/): root
  manifest, lockfile, and environment conventions.
- [Pydantic models](https://pydantic.dev/docs/validation/latest/concepts/models/):
  validation and serialization boundaries.
- [GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini):
  the requested model supports structured outputs; account access remains untested.
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs):
  provider schema restrictions and refusal handling for A2.

## Progress log

- 2026-09-13: Read project planning and DOCX stack reference. Existing workspace
  contains documents and an empty `agents/` directory. uv 0.10.9 is available;
  root `.venv` created with installed CPython 3.12.13.
- 2026-09-13: **A1 complete.** Shared wire models, pure initial-graph validator,
  three goal/graph fixture pairs, and 58 offline tests are implemented. pytest:
  58 passed; Ruff lint and format checks passed; `uv pip check`: 90 installed
  packages compatible; `uv sync --locked --all-extras` reproduced the environment.
  uv resolved 91 lock entries including the local project. Tests required a
  sandbox escalation because a uv-installed dependency was unreadable inside
  the sandbox; no credentials or external applications were used by the tests.
- 2026-09-13: User verified A1 and authorized A2. **A2 complete.** 120 offline
  tests pass, including SDK parsing over a mock transport, error classification,
  one validation retry, stable clock/IDs, closed schemas, recursive JSON mapping,
  and source-data separation. Ruff checks pass. A live smoke CLI is available
  but has not been run against a real model account.
  Declared `tzdata` directly for Windows timezone support and `httpx2` as the
  SDK test-transport dependency; updated `uv.lock`. Final uv test invocation
  used `--basetemp=.schema-review/pytest-a2` after Windows denied access to the
  default pytest temp directory. Full locked environment sync also passed.
- 2026-09-13: Reviewed teammate-provided SQL on isolated PostgreSQL 16. Original
  schema runs; review probes confirmed integrity gaps. Proposed constraints and
  regression SQL passed locally. Original SQL and shared DTOs are unchanged.
  The isolated review server was stopped after the tests.
  See [SQL review and teammate handoff](LoopGraph_08_Schema_Review.md).
- **Next: A3, Outcome Compiler.** Implement the generic compiler prompt and service
  on the A2 boundary. A4 evidence semantics, A5 repair validation, full-state
  replan context, live model quality, and hosted Supabase integration remain open.
