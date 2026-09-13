# Teammate C handoff — schema.sql review

Reviewed 2026-09-13 against the supplied SQL, the shared Pydantic models,
contracts document, ownership map, and A1/A2 plan.

**Verdict: the schema is a sound starting point and executes successfully, but
needs changes before graph persistence and frontend integration.** The original
`supabase/schema.sql` has been left unchanged. No shared or hosted database was
modified, and no message has been sent to the teammate.

## What already aligns

- All nine domain tables use the expected names and uppercase state vocabularies.
- IDs are application-supplied text; timestamps use `timestamptz`; JSON fields fit
  the domain payloads; evidence and actions remain separate records.
- Action status distinguishes EXECUTED from VERIFIED. Evidence assessment's
  `verified` flag correctly remains independent of its relationship classification.
- Events enforce uniqueness of `(source_app, external_id)` for non-null external
  IDs. Actions enforce unique idempotency keys. These constraints passed probes.
- Explicit edges, duplicate semantic-edge prevention, self-edge rejection, due-node
  index, and updated-at triggers support the intended runtime design.

## Required integrity changes

### 1. Require an existing root in the same loop — schema.sql:24

`loops.root_node_id` is nullable and has no foreign key. A committed loop can
reference nothing, a missing node, or another loop's node. All three cases were
accepted by the local probe. They violate the compiled-graph contract and can
produce an unrenderable or incorrectly completed graph.

Add `UNIQUE (loop_id, id)` to `outcome_nodes`, make `root_node_id` non-null, and
use a composite foreign key `(loops.id, loops.root_node_id)` referencing
`outcome_nodes(loop_id, id)`, `DEFERRABLE INITIALLY DEFERRED`.

**Insertion must be one transaction:** insert the loop with its preallocated root
ID, insert its nodes/edges/requirements/actions, then commit. Independent Supabase
REST insert requests do not share a database transaction. C should provide a
transactional database function/RPC or transactional Postgres persistence method.
The deferred FK permits the forward reference inside that transaction. PostgreSQL
supports composite foreign keys and deferred constraint checking.
[PostgreSQL constraints](https://www.postgresql.org/docs/16/ddl-constraints.html),
[CREATE TABLE](https://www.postgresql.org/docs/17/sql-createtable.html).

### 2. Enforce loop ownership on related rows — schema.sql:56, 128, 155

Independent foreign keys only prove that a loop and a node/action exist. They do
not prove that they belong together. The probe accepted:

- an edge in loop A pointing to a node in loop B;
- an action in loop A assigned to a node in loop B;
- an approval in loop B approving an action in loop A.

Use composite `(loop_id, node_id)` references for action nodes, and corresponding
composite references for both edge endpoints. Add `UNIQUE (loop_id, id)` on actions
and use `(loop_id, action_id)` for approvals. Preserve the intended deletion
cascades. A1 validates compiler proposals, but persistence must protect writes
from every entry point, including repair application.

### 3. Store source-event provenance — loops/events mapping

`Loop.source_event_ids` has no durable representation. `events.linked_loop_id`
cannot distinguish the goal's source events from subsequent observations and
cannot represent an event contributing to more than one loop.

Recommended: a `loop_source_events(loop_id, event_id)` association table with a
composite primary key and foreign keys. Populate it when saving compilation and
derive `Loop.source_event_ids` from it when loading. Keep `linked_loop_id` as the
existing optional routing hint; it is not the source-provenance relation.

### 4. Match evidence and JSON field shapes — schema.sql:99–104 and JSONB columns

The SQL defaults `source_apps` to an empty array, while A1 requires at least one
nonblank source. Empty source lists passed the probe. Drop the empty default and
add a nonempty array constraint (also reject null elements). Continue validating
nonblank app names at the Pydantic boundary.

Postgres JSONB accepts any JSON shape: the probe stored `{}` in `attachments`,
which must be a list. Add `jsonb_typeof` checks: attachments must be an array;
metadata, required_fields, extracted_fields, and parameters must be objects.
These checks constrain the outer shape; nested attachment entries and field
semantics still require the normal DTO/business validators.

### 5. Preserve proposal risk flags — schema.sql:128–147

The SQL accepts `SEND_EMAIL / MEDIUM / requires_approval=false`. A1 rejects that
combination. Add static checks that medium-risk actions require approval and that
email/Slack sends plus external Calendar updates/cancellations carry medium risk
and approval. Calendar updates follow the supplied DOCX policy.

These checks do **not** implement approval enforcement. C/B must still require
the current approval before execution, reject unsupported high-risk operations,
and validate state transitions. A historical action may retain
`requires_approval=true` after it has been approved or executed.

## Integration decisions to resolve

### Browser access and RLS — before frontend Supabase access

All nine original tables have RLS disabled, confirmed through `pg_tables`.
This file also supplies no grants or access policies. With the planned browser
Supabase/Realtime access, the schema alone does not enforce the rule that the
frontend must not write business state.

C/D should either configure backend-only database access with controlled event
delivery, or enable RLS, grant only appropriate read access, and add owner-based
SELECT policies for authenticated Realtime clients. Keep business writes in the
backend. Actual exposure depends on the deployed database's grants; the local
review does not claim to have audited a hosted Supabase project. SQL-created
tables require explicit RLS configuration.
[Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).

`user_id` may stay text for the fixed demo user. Do not add an `auth.users` FK
blindly: first agree whether IDs are real Supabase Auth UUID strings or demo IDs.
An owner policy using `auth.uid()::text` only works with the former convention.

### Realtime evidence requirements — schema.sql:179–185

The publication omits `evidence_requirements`. An edited declaration-page
requirement can leave the evidence panel stale if no subscribed table changes.
Add the table to the publication, or explicitly have the UI refetch requirements
on every repair activity. The SQL proposal adds it. Publication membership is
necessary for Postgres Changes subscriptions; access policies and client
subscriptions must be tested separately.
[Supabase Postgres Changes](https://supabase.com/docs/guides/realtime/postgres-changes).

### Persist compilation assumptions and pending clarification

`CompiledGraph.assumptions`, `clarification_needed`, and `clarification_question`
are response fields, not fields on the `Loop` DTO, so their absence from `loops`
is not a direct field-name mismatch. Nevertheless, C must choose where to retain
them across restart. Use an intake/compilation record or explicit columns selected
separately from `Loop`. A3 must not lose a pending clarification or execute guessed
actions after reload. This storage decision is not imposed by the proposal.

### Idempotency and reset semantics

Correct the comment at line 146: the unique key prevents a second **row**, not a
second **execution of the same row**. C/B need atomic action claiming, replay
checks, provider idempotency where available, and reconciliation after uncertain
external writes. Similarly, the `processed` flag plus a unique event row does not
itself serialize two workers processing that row.

Deleting a loop does not remove its events: `linked_loop_id` becomes null and the
external dedup key remains. That preserves history, but a demo reset that reuses
the same external fixture IDs must explicitly handle those fixture events or use
new occurrence IDs. Do not describe one loop delete as a complete demo reset.

## Fields that should be derived, not duplicated

The missing node ID arrays are normal relational normalization, not defects:

| Domain field | Reconstruct from |
|---|---|
| `Loop.node_ids` | `outcome_nodes` filtered by loop |
| `OutcomeNode.depends_on` | DEPENDS_ON edges, source = dependent, target = prerequisite |
| `OutcomeNode.evidence_requirement_ids` | `evidence_requirements.node_id` |
| `OutcomeNode.evidence_ids` | `evidence.node_id` |
| `OutcomeNode.action_ids` | `actions.node_id` |

C should provide explicit database-to-DTO mapping. In particular, the SQL's extra
`events.created_at` must be excluded when building the strict `Event` DTO. The
hydrated graph must agree with the authoritative edges. Graph-cycle checks,
evidence satisfaction, controlled reopening, and repair history remain runtime
and intelligence responsibilities; ordinary foreign keys cannot implement them.

## Concrete proposal and verification

See [proposed_contract_constraints.sql](../supabase/review/proposed_contract_constraints.sql).
It includes root/ownership constraints, source provenance, source/JSON checks,
static risk flags, and the missing publication table. It is a review artifact,
not an applied migration. It requires clean existing data and transactional graph
insertion. It deliberately leaves auth policies and clarification storage to C/D.

Executed in a fresh disposable local PostgreSQL 16 database:

1. Original schema: successfully created all nine tables, triggers, and indexes.
2. [Original-schema probe](../supabase/tests/schema_review_probe.sql): demonstrated
   all nine listed invalid states, while duplicate event/action keys and invalid
   event enums were correctly rejected. Probe rows rolled back.
3. Proposed SQL: applied successfully to that disposable database.
4. [Constraint regression script](../supabase/tests/proposed_constraints_test.sql):
   all nine invalid-state checks rejected; transactional graph insertion,
   source-event linkage, and loop deletion cascades passed.
5. Local publication membership check: original seven tables plus the proposed
   evidence_requirements entry. This does not test hosted Realtime delivery/RLS.

The teammate should review the proposal alongside the persistence transaction
design, then turn accepted changes into a versioned migration. The base schema
is a one-time bootstrap script, not a rerunnable migration.

## Message you can send

> The schema runs and the tables/enums align. Before integration, please add a
> non-null deferred same-loop root FK, composite same-loop FKs for edges/actions/
> approvals, durable source-event links, nonempty evidence sources, and JSON shape
> checks. I've attached a tested proposal in supabase/review. We also need to agree
> on RLS/read-only frontend access, clarification/assumption persistence, and
> evidence-requirement Realtime updates. Graph inserts must be one transaction/RPC.
> Unique action keys prevent duplicate rows; the executor still needs replay and
> concurrent-execution protection.
