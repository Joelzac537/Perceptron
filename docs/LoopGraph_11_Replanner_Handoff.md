# A5 — Replanner handoff

A5 returns validated operations against the existing graph. It does not replace
the graph, execute app actions, persist changes, or perform evidence-backed node
verification. The user authorized finishing A5 while C is unavailable; the local
context interface below makes that possible without changing shared DTOs.

## Service and full-state context

```python
from datetime import UTC, datetime

from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.agents.replanner import Replanner
from app.config import Settings
from app.graph.repair_schemas import RepairInput, ReplanContext
from app.graph.repair_validation import preview_repair
from app.graph.schemas import ReplanRequest


async def propose_repair(request: ReplanRequest, context: ReplanContext):
    settings = Settings.from_env()
    reference_time = datetime.now(UTC)
    snapshot = RepairInput.model_validate_json(
        RepairInput(request=request, context=context).model_dump_json()
    )
    async with OpenAIProvider(settings) as provider:
        boundary = ReasoningBoundary(provider, settings, clock=lambda: reference_time)
        result = await Replanner(boundary).replan_with_metadata(
            snapshot.request, context=snapshot.context
        )
    candidate = preview_repair(snapshot, result.value, as_of=reference_time)
    return result, candidate  # In-memory only; application is a separate runtime step.
```

`replan(request, *, context=context)` returns the existing `ReplanResponse`.
`replan_with_metadata` also returns prompt/boundary versions, attempts, actual
model, latency, and usage. The prompt is `replanner-v1`, using A2's medium reasoning
effort, strict `ReplanDraft`, and one validation repair attempt.

`ReplanRequest` lacks the full requirements, actions, and evidence needed to check
repairs. The required keyword-only `ReplanContext` supplies them:

| Field | Caller responsibility |
|---|---|
| `state_revision` | Nonempty opaque revision of the complete loaded state, for a later compare-and-swap/transaction check. |
| `requirements` | All requirements referenced by all loop nodes, including history. |
| `actions` | All node-referenced and loop-level actions, with actual status, parameters, idempotency keys, and observed external IDs. |
| `evidence` | All evidence referenced by loop nodes, including insufficient or contradictory assessments. |
| `available_apps` | Actual currently usable integration names. |
| `applied_event_ids` | Trigger IDs already atomically applied by runtime; defaults to an empty list for initial integration. |

The shared request supplies the full Loop, all nodes and edges, the triggering
Event, and assessed A4 decisions. Context coverage, same-loop references, node
lists, dependencies, edge agreement, cycles, and active-root reachability are
validated before any model call. The service snapshots mutable input first.
Historical CANCELLED/SUPERSEDED nodes remain in the graph and may be disconnected;
live nodes must remain reachable from the root and cannot depend on those nodes.

Existing VERIFIED states and A4 decisions are trusted runtime inputs. A5 does
not authenticate source messages or re-run A4 to establish their correctness.
C must bind decisions to the trigger and load an authorized, consistent snapshot.

## Operation conventions

Provider payloads are closed, typed locally after decoding A2's JSON entries.
The full model-facing conventions are in
[replanner.md](../backend/app/prompts/replanner.md). Every operation has a reason.
Unknown fields, malformed timestamps, invalid targets, and conflicting repeated
mutations fail validation. New-object refs never become persisted IDs directly.

| Operation | Behavior in the normalized response and preview |
|---|---|
| ADD_NODE | Full new OutcomeNode payload with allocated ID and local timestamps. No initial references or evidence; attach them through explicit operations. |
| UPDATE_NODE | Descriptive title/description/recovery fields only, with source_quote. Root descriptive edits require assessed changed evidence or user input. |
| SUPERSEDE_NODE | Existing unresolved nonroot node becomes SUPERSEDED; payload identifies the newly added replacement and quotes the source change. All dependency rewiring must be explicit. |
| CANCEL_NODE | Unresolved nonroot node becomes CANCELLED after a changed-evidence/user-input check. Preserve history and explicitly reconcile dependencies. |
| VERIFY_NODE | Recognized and rejected. Runtime owns evidence-backed verification and completion gates. |
| ADD_EDGE | Full new Edge with allocated ID; only DEPENDS_ON or a SUPERSEDES edge matching the actual replacement. |
| REMOVE_EDGE | Empty payload targeting an existing dependency edge. Evidence/history edges cannot be removed. |
| UPDATE_DEADLINE | Aware deadline or null, plus source_quote. Existing pending work must be reconciled. |
| ADD_EVIDENCE_REQUIREMENT | Full new requirement. An existing node additionally needs source_quote and assessed changed evidence/user input. |
| UPDATE_EVIDENCE_REQUIREMENT | Replaces the criteria under the existing requirement ID, preserving creation identity; needs source_quote and assessed change/user input. |
| ADD_ACTION | Optional `{action_id}` marker for a proposal already present in proposed_actions. Never a second action to insert or execute. |
| CANCEL_ACTION | Empty payload targeting an unexecuted internal action. Execution history is preserved. |

Addition target_id is null; mutations target existing IDs. Operations are ordered:
add nodes before requirements/edges, remove old dependencies before adding
replacements, and then supersede the old owner. Validation runs on a copy after
the entire sequence, so intermediate graph fragments never escape as committed
state. A failure rejects the whole proposal and leaves caller inputs unchanged.

Dependency operations update both Edge records and depends_on arrays. New nodes
and nodes with changed prerequisites become ACTIVE if all prerequisites are
VERIFIED, otherwise BLOCKED. This never verifies an outcome. Relative-deadline
metadata (`deadline_prerequisite_node_id`) follows an owner replacement when the
dependent's prerequisite is rewired. The preview updates changed timestamps;
the runtime must apply these derived fields consistently within its transaction.

## Preservation and changed requirements

Owner replacement preserves the old node's historical fields, original
requirements, assessed evidence, and executed actions. The new node has a
different owner, the same structured evidence criteria, identity metadata, and
prerequisites. Every live dependent must switch from old to new. Root ID, loop
goal, and unaffected objects remain intact. An optional SUPERSEDES edge records
new-to-old provenance; it must match the actual replacement.

Changing evidence criteria is separate from owner reassignment. Adding/updating
requirements on an existing node or cancelling a node needs a source_quote from
the triggering event and either USER_INPUT or an A4 SUPERSEDES/CONTRADICTS decision
for that node. The new criteria must use available apps and must_all_match=true.
This supports the policy-to-declaration-page case without deleting requirement
history. Already VERIFIED/CANCELLED/SUPERSEDED nodes cannot have requirements
rewritten. A5 preserves Evidence objects; runtime must reassess old evidence
against changed requirements rather than reusing an old satisfaction flag.

Source quotes are checked for literal occurrence, not semantic entailment.
Whether a passage really authorizes a requirement change remains model reasoning.
Date arithmetic, including the prompt's weekday-only business-day convention,
also remains semantic work; deterministic validation checks aware timestamps
and consistency of Calendar proposals with the resulting node deadline. Holiday
calendars are not implemented. Do not interpret these checks as measured live
model accuracy.

## Actions, cleanup, and replay

Changing deadlines, requirements, or dependencies, or resolving/superseding a
node, requires cancellation of its obsolete pending internal actions. A5 permits
CANCEL_ACTION only for PROPOSED, AWAITING_APPROVAL, APPROVED, or FAILED actions
with no external_id. It rejects cancellation of EXECUTING, EXECUTED, VERIFIED,
or already CANCELLED history. In-flight work on an affected node must be
reconciled before applying a repair.

Verified Calendar creation/update history is external state. A changed deadline
needs an approval-gated update or cancellation; a resolved node needs
cancellation. UPDATE/CANCEL_CALENDAR_EVENT must reference a verified external_id
on the same node and retain MEDIUM risk, approval, and READ_AFTER_WRITE checking.
The original action stays VERIFIED. A verified cancellation already recorded in
history is sufficient. Closed loops accept cleanup only. Non-calendar proposals
cannot run on blocked/pending nodes or introduce new work on resolved nodes.

Other proposals retain A3's action vocabulary and parameter rules. Email routing
must be explicit in the event or already bound by an email action to that node;
Slack channel/thread comes from source Slack metadata. Calendar start must equal
the resulting node deadline; end must be later. Unknown apps, invented external
targets, extra routing fields, missing approval, and duplicate proposals fail.

IDs are hashes of loop ID, triggering event ID, object kind, and normalized object
identity. Owner replacements use the old node ID, not the temporary ref. Send
identity uses destination rather than message wording; rewording a request in
the same event cannot create another executable identity. Equivalent pending
actions, normalized title/owner duplicate nodes, duplicate semantic edges, and
attempts to replace an already superseded owner are rejected. This is not a
general semantic equivalence engine for arbitrary paraphrased new outcomes.

An event in applied_event_ids returns an empty repair before calling the model.
Otherwise, repeated proposals remain subject to ID/semantic duplication checks.
The preview does not append applied_event_ids, advance state_revision, create
ActivityLog rows, or commit anything. Those changes belong to runtime application.

## Runtime integration checklist for later

1. Load the complete authorized snapshot and remember its state_revision.
2. Run A4 and runtime completion gates as appropriate; call A5 with consistent
   current state and assessments. VERIFY_NODE from a planner is never accepted.
3. Recheck the expected revision and event dedupe marker inside one transaction.
   Replan against a fresh snapshot after a conflict; do not apply a stale preview.
4. Persist the validated operations and derived fields atomically, preserving
   history. Insert each proposed action once, including optional ADD_ACTION markers
   only as references. Record one coherent activity entry and applied trigger ID.
5. Schedule/execute only after commit. Enforce approval at execution, recheck
   action/node state to avoid races, and perform B's read-after-write verification.

C's final adapter agreement and the transaction implementation are deferred per
the user's instruction. No teammate messages, database writes, or app calls were
made for this milestone. The [A4 aggregation policy](LoopGraph_10_Verifier_Handoff.md)
remains a separate integration decision.

## Validation

The full offline suite passes: 295 tests, including 58 A5 tests. Coverage includes
R-01/02, RR-07/08 repair behavior, PR-06, ID-03, RC-01 through RC-04, history and
root preservation, unknown targets, cycles, missing cleanup, invalid approval,
external-ID grounding, replay, snapshot isolation, and invalid-output retry.
The actual SDK parses ReplanDraft over mocked HTTP with medium reasoning effort.
All model outputs in these checks are scripted; live semantic and runtime/app
acceptance remain A6 work.
