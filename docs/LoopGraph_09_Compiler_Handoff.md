# A3 — Outcome Compiler handoff

Implementation lives under the Git/project root `LoopGraph/Perceptron`. Run uv,
tests, and CLI commands there; select `Perceptron/.venv` in the outer workspace IDE.

## Service contract

```python
async OutcomeCompiler.compile(CompileGoalRequest) -> CompiledGraph
```

Construct the service with `ReasoningBoundary(provider, settings, clock, id_factory)`.
The caller owns and closes the provider's async client. The service snapshots and
revalidates the request before awaiting the model, then invokes `compiler-v1`
through the existing A2 structured-output path. One validation failure permits
one repair. A second raises `LLM_OUTPUT_INVALID`. Provider error codes remain
unchanged. No persistence, integration calls, or action execution occurs here.

Use `compile_with_metadata()` to get a `ReasoningResult[CompiledGraph]` for activity
or evaluation instrumentation. It contains the actual response model when one is
available, prompt/boundary versions, latency, usage, and attempt outcomes. There is
no mutable service-level last-result field; concurrent calls remain independent.

`user_id`, source-event IDs, timestamps, generated IDs, and initial states come from
trusted request/mapping code. Model output cannot approve actions or verify nodes.
An input event already linked to a loop raises `CompilerInputError` with code
`VALIDATION_ERROR` before a model call; route that event through reconciliation.
Other malformed requests raise Pydantic `ValidationError`. Event deduplication and
durable cross-call action idempotency remain C's responsibilities.

## Runtime treatment of clarification

Missing facts that can be retrieved with a safe supported search may produce a
search proposal while dependent actions stay deferred. Essential unresolved or
contradictory information produces:

- `clarification_needed = true` with a nonblank question;
- a BLOCKED loop and BLOCKED nodes;
- no proposed actions;
- known outcomes/evidence contracts retained for review.

If the objective itself is unknown, one provisional clarification outcome using
HUMAN_CONFIRMATION from `loopgraph` is allowed. This is a review draft, not an
actionable goal. C must persist the compilation question and assumptions, show
the question, and avoid scheduling/execution. On a user response, incorporate it
into intake context and replace the pending draft transactionally or otherwise
resolve that pending record; do not leave duplicate active loops. The final
persistence/clarification endpoint remains C's implementation responsibility.

## Timing

The prompt resolves source-relative language using source_event.timestamp in the
configured timezone; user-entered relative dates use the supplied reference time.
Aware timestamps are required. An assumed 17:00 checkpoint for a known day must
be disclosed. Missing dates remain null. Interpreting natural-language dates and
checking whether an assumption is reasonable still require live semantic evaluation.

For timing contingent on an unobserved prerequisite, compiler provider metadata is:

```json
{
  "deadline_rule": "5 business days after merchant receipt",
  "prerequisite_ref": "merchant_receipt"
}
```

These are values represented through the provider's existing key/value DTO. The
mapper requires both fields, checks that the ref is a direct dependency, and
rejects a premature concrete deadline. Domain metadata becomes:

```json
{
  "deadline_rule": "5 business days after merchant receipt",
  "deadline_prerequisite_node_id": "node_<generated-id>"
}
```

C/A5 can resolve the rule when evidence supplies the prerequisite's occurrence
time. This is not a scheduler implementation or a general date-expression DSL.
Weekdays are the initial business-day convention; holidays need an explicit policy.

## Initial action proposal conventions for B/C

Shared Action fields remain unchanged. Since the contract's `parameters` is an
open dictionary, A3 documents and validates this limited initial proposal subset.
B must translate it into Composio-specific arguments; these are not raw Composio
tool payloads. Additive future conventions must be coordinated before enabling them.

| Action | App | Required parameters | Verification |
|---|---|---|---|
| SEARCH_GMAIL | gmail | query | API_STATE |
| SEARCH_DRIVE | google_drive | query | API_STATE |
| CREATE_CALENDAR_EVENT | google_calendar | title, start, end | READ_AFTER_WRITE |
| SAVE_DRIVE_FILE | google_drive | attachment_id | READ_AFTER_WRITE |
| SEND_EMAIL | gmail | to, subject, body | READ_AFTER_WRITE |
| SEND_SLACK_MESSAGE | slack | channel_id, message; optional thread_ts | READ_AFTER_WRITE |

Values must be nonempty strings and undeclared keys are rejected. Email `to` is
one literal address supported by the user goal/source actor/subject/content.
Slack routing identifiers must match source Slack metadata. Drive saves must
reference an attachment's supplied `id` or `attachment_id`; the executor fetches
its bytes through the integration, not from an invented URL. Searches can discover
missing data for later actions. Email attachment submission is intentionally
deferred until prerequisite evidence exists; it is not part of initial SEND_EMAIL.

All initial proposals attach to an outcome node. Actions for unresolved dependent
nodes are deferred, except a Calendar checkpoint for a known deadline. Calendar
times must be aware and end after start. Natural-language date grounding and the
choice of reminder time are not proven by this interval check.

Email/Slack sends must be MEDIUM and AWAITING_APPROVAL. The A1 risk validator still
rejects unsupported/high-risk actions. Calendar changes/cancellations require
existing state and belong to reconciliation, so the compiler defers them. No
intelligence output authorizes execution. B/C must validate recipient intent,
current approval, parameter mapping, and external state at execution time.

Evidence sources must be in available_apps or use `loopgraph` for manual evidence.
The source app is not implicitly considered connected merely because an old event
is supplied. A future verifier must retrieve/inspect relevant artifacts; compiler
requirements are promises of what evidence will be checked, not proof of completion.

## Verification and limits

Offline coverage includes the three original graph fixtures; three goal
paraphrases; an unseen projector-repair obligation; malformed references/cycles;
source identity and request snapshotting; clarification; unknown relative dates;
grounded email/Slack/attachment routing; unsafe actions; and a full compiler → SDK
→ validation-repair round trip with mock HTTP responses.

Scripted provider tests assert graph invariants and wiring. They do not prove
that a live model generates the expected graph or handles paraphrases correctly.
The prompt is shared across scenarios; there is no scenario-specific production
branch or deterministic fallback graph. A wrong but structurally valid goal,
incomplete evidence semantics, or misleading input may still need live evaluation.

For live checks, use `python -m app.agents.compile_goal --live` with a JSON
CompileGoalRequest. The CLI emits graph plus diagnostics and does not execute it.
Review using the original acceptance cases RR-01, PR-01, C-01/02/03 and renewal
compilation; compare outcomes/dependencies/evidence, not exact titles or IDs.
`backend/tests/fixtures/compiler_cases.json` adds paraphrases and review criteria.
No live model evaluation was run during this milestone.

The supplied model and reasoning effort are retained from the stack reference.
Prompt separation and explicit objectives follow
[OpenAI prompt guidance](https://developers.openai.com/api/docs/guides/prompt-engineering).
