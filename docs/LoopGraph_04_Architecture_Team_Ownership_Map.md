# LoopGraph — Architecture & Team Ownership Map

## 1. Purpose

This document defines how the LoopGraph system is split across the four-person team.

The goal is to allow parallel development with minimal coupling.

Each teammate should know:

- what they own
- what inputs they consume
- what outputs they expose
- what they must not implement
- which shared contracts they depend on
- when their work must integrate with other branches

The system should remain modular enough that one component can be replaced without forcing large changes elsewhere.

---

# 2. High-Level Architecture

```text
                         ┌─────────────────────┐
                         │     Next.js UI      │
                         │   React Flow + UI   │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      FastAPI        │
                         │     REST Layer      │
                         └──────────┬──────────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  │                 │                 │
                  ▼                 ▼                 ▼
         ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
         │ Outcome        │ │ Runtime /      │ │ Integrations   │
         │ Intelligence   │ │ Reliability    │ │ Layer          │
         │                │ │                │ │                │
         │ Compiler       │ │ Event Router   │ │ Gmail          │
         │ Verifier       │ │ Scheduler      │ │ Slack          │
         │ Replanner      │ │ Persistence    │ │ Drive          │
         └───────┬────────┘ │ State Machine  │ │ Calendar       │
                 │          └───────┬────────┘ └───────┬────────┘
                 │                  │                  │
                 └──────────────────┼──────────────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Supabase/Postgres   │
                         │  Product Source     │
                         │      of Truth       │
                         └─────────────────────┘
```

Supporting services:

```text
LangGraph
→ orchestration / resumable execution

Composio
→ external-app integrations

GPT-5.4 Mini
→ structured reasoning

APScheduler
→ missed-deadline events

Supabase Realtime
→ live UI updates
```

---

# 3. Team Structure

Recommended ownership:

```text
Teammate A
AI / Outcome Intelligence

Teammate B
External App Integrations

Teammate C
Runtime / Reliability / Persistence

Teammate D
Frontend / Product / Demo
```

One person should also act as the temporary integration lead.

Recommended:

```text
Teammate C
```

because the runtime layer touches all contracts.

This is an operational responsibility, not extra feature ownership.

---

# 4. Teammate A — Outcome Intelligence

## 4.1 Primary Ownership

Owns all semantic reasoning related to:

```text
Goal → Outcome Graph

Event → Evidence Meaning

Changed Reality → Graph Repair
```

Specific modules:

```text
Outcome Compiler
Evidence Verifier
Replanner
Prompt design
Structured LLM outputs
Graph semantic validation
```

---

## 4.2 Files / Modules

Suggested ownership:

```text
backend/app/agents/compiler.py
backend/app/agents/verifier.py
backend/app/agents/replanner.py

backend/app/graph/semantic_validation.py

backend/app/prompts/
```

May contribute to:

```text
backend/app/graph/schemas.py
```

but shared schemas should be coordinated with Teammate C.

---

## 4.3 Inputs Consumed

From shared contracts:

```text
CompileGoalRequest
Event
Loop
OutcomeNode
Edge
EvidenceRequirement
VerifyEventRequest
ReplanRequest
```

---

## 4.4 Outputs Exposed

Must expose:

```python
OutcomeCompiler.compile(...)
→ CompiledGraph
```

```python
EvidenceVerifier.verify(...)
→ VerifyEventResponse
```

```python
Replanner.replan(...)
→ ReplanResponse
```

---

## 4.5 Required MVP Behavior

### Compiler

Must reliably produce:

```text
Loop
Nodes
Edges
Evidence Requirements
Proposed Actions
```

for all three use cases.

Must work with:

```text
Return / Refund
Promise
Paperwork / Renewal
```

without scenario-specific branching.

---

### Verifier

Must classify events as:

```text
PROVES
CONTRADICTS
SUPERSEDES
PARTIALLY_SUPPORTS
INSUFFICIENT
UNRELATED
```

Must handle at minimum:

```text
valid refund confirmation
wrong refund/order
final presentation
draft presentation
new insurance policy
old insurance policy
changed owner
changed requirement
```

---

### Replanner

Must support at least:

```text
deadline update

owner reassignment

changed evidence requirement

stale action cancellation

new node creation

old node supersession
```

---

## 4.6 What Teammate A Must NOT Own

Do not directly:

```text
call Gmail

call Slack

call Drive

call Calendar

write frontend state

create webhook handlers

persist directly into database from agent prompts

execute external actions
```

Reasoning should produce structured outputs.

Execution belongs elsewhere.

---

## 4.7 Definition of Done

Teammate A is done when:

- [ ] all structured outputs validate through Pydantic
- [ ] no reasoning output requires regex parsing
- [ ] three use cases compile into generic graph structures
- [ ] verifier handles positive and insufficient evidence
- [ ] replanner produces graph operations, not regenerated graphs
- [ ] unit tests cover key semantic cases
- [ ] mock interfaces work without external apps

---

# 5. Teammate B — Integrations

## 5.1 Primary Ownership

Owns all interaction with external applications.

Specific integrations:

```text
Gmail
Slack
Google Drive
Google Calendar
Composio
Webhook handling
Action execution
Action verification
```

---

## 5.2 Files / Modules

Suggested ownership:

```text
backend/app/integrations/composio.py

backend/app/integrations/gmail.py
backend/app/integrations/slack.py
backend/app/integrations/drive.py
backend/app/integrations/calendar.py

backend/app/services/executor.py
backend/app/services/action_verification.py

backend/app/api/webhooks.py
```

---

## 5.3 Inputs Consumed

Primary input:

```text
Action
```

Webhook inputs:

```text
raw Composio trigger payloads
```

Shared output:

```text
Event
ExecuteActionResponse
VerifyActionResponse
```

---

## 5.4 Outputs Exposed

Must expose approximately:

```python
ActionExecutor.execute(action)
→ ExecuteActionResponse
```

```python
ActionVerifier.verify(action, result)
→ VerifyActionResponse
```

```python
normalize_composio_event(payload)
→ Event
```

---

## 5.5 Required Integration Coverage

### Gmail

Minimum:

```text
search messages
read message/thread
read attachments
draft/send reply
verify sent message
```

---

### Slack

Minimum:

```text
read message/thread
send message
verify message exists
```

---

### Google Drive

Minimum:

```text
search file
upload/save file
retrieve file metadata
verify file exists
```

---

### Google Calendar

Minimum:

```text
create event
update event
delete/cancel event
read event
verify expected state
```

---

## 5.6 Integration Priority

If implementation time becomes limited:

```text
1. Gmail
2. Calendar
3. Slack
4. Drive
```

However, all three use cases should ideally touch Drive.

---

## 5.7 Read-After-Write Verification

Every write action should attempt verification.

Example:

```text
CREATE_CALENDAR_EVENT
        ↓
Calendar API success
        ↓
Read event back
        ↓
Compare expected title/time
        ↓
VERIFIED
```

Do not treat:

```text
HTTP 200
```

as sufficient proof.

---

## 5.8 Idempotency

Before performing an action:

```text
check action.idempotency_key
```

If an equivalent action is already:

```text
VERIFIED
```

do not repeat it.

---

## 5.9 What Teammate B Must NOT Own

Do not:

```text
decide what a goal means

decide if evidence semantically proves a node

modify graph topology

generate recovery plans

implement frontend workflow logic

invent new business states
```

Integrations execute structured requests.

They do not reason about the goal.

---

## 5.10 Definition of Done

- [ ] Gmail read/write works
- [ ] Slack read/write works
- [ ] Calendar create/read/update works
- [ ] Drive search/save works
- [ ] external events normalize into shared Event schema
- [ ] action executor accepts shared Action model
- [ ] write actions support read-after-write verification
- [ ] duplicate action execution is prevented
- [ ] external error results map to shared error contract

---

# 6. Teammate C — Runtime, Reliability & Persistence

## 6.1 Primary Ownership

Owns the system glue.

This teammate is responsible for:

```text
LangGraph orchestration
Event routing
State transitions
Persistence
Supabase
Scheduler
Idempotency
Graph operation application
Activity logging
API orchestration
```

---

## 6.2 Files / Modules

Suggested ownership:

```text
backend/app/graph/workflow.py
backend/app/graph/state.py
backend/app/graph/schemas.py

backend/app/events/router.py
backend/app/events/normalizer.py

backend/app/services/scheduler.py
backend/app/services/graph_ops.py
backend/app/services/state_machine.py
backend/app/services/activity.py

backend/app/db/
backend/app/api/loops.py
backend/app/api/approvals.py

supabase/schema.sql
```

---

## 6.3 Inputs Consumed

Consumes outputs from:

```text
Compiler
Verifier
Replanner
Integrations
Scheduler
Frontend approvals
```

---

## 6.4 Outputs Exposed

Provides:

```text
REST endpoints
persisted state
realtime updates
event pipeline
approval state
loop detail DTOs
```

---

## 6.5 Core Runtime Responsibilities

### Loop Creation

```text
Goal/Event
↓
Compiler
↓
Validate
↓
Persist Graph
↓
Create Actions
↓
Risk Check
↓
Execute / Approval
```

---

### Event Processing

```text
Event
↓
Deduplicate
↓
Persist
↓
Route
↓
Verifier
↓
Update / Replan
↓
Create Actions
↓
Execute / Approval
↓
Persist
```

---

### Deadline Handling

APScheduler emits:

```text
DEADLINE_REACHED
```

events.

The scheduler itself should not perform follow-ups.

---

### Replanning

Apply graph operations transactionally where possible.

Example:

```text
SUPERSEDE old node

ADD new node

ADD edge

CANCEL stale action

ADD new action
```

should appear as one coherent graph change.

---

## 6.6 Persistence Rules

Supabase/Postgres is authoritative for:

```text
Loops
Nodes
Edges
Events
Evidence
Actions
Approvals
Activity
```

LangGraph checkpoints are:

```text
execution state
```

not the business source of truth.

---

## 6.7 API Ownership

Suggested endpoints:

```text
POST /api/loops
GET  /api/loops
GET  /api/loops/{loop_id}

GET  /api/approvals
POST /api/approvals/{id}/approve
POST /api/approvals/{id}/reject

POST /api/webhooks/composio
POST /api/events
```

---

## 6.8 What Teammate C Must NOT Own

Do not:

```text
build full frontend

write app-specific integration logic

own prompt semantics

duplicate reasoning logic
```

This teammate orchestrates existing components.

---

## 6.9 Definition of Done

- [ ] shared schemas available to all teammates
- [ ] Supabase schema created
- [ ] create/get loop APIs work
- [ ] normalized event pipeline works
- [ ] event deduplication works
- [ ] action idempotency is enforced
- [ ] graph operations apply correctly
- [ ] APScheduler produces deadline events
- [ ] approval transitions work
- [ ] activity log records important state changes
- [ ] LangGraph flow can pause/resume

---

# 7. Teammate D — Frontend, Product & Demo

## 7.1 Primary Ownership

Owns the user-facing product.

Specific components:

```text
Next.js
React Flow graph
Loop dashboard
Activity feed
Evidence panel
Approval UI
Integration status
Realtime updates
Demo polish
```

---

## 7.2 Files / Modules

Suggested ownership:

```text
frontend/app/

frontend/components/LoopGraph.tsx
frontend/components/OutcomeNode.tsx
frontend/components/ActivityFeed.tsx
frontend/components/EvidencePanel.tsx
frontend/components/ApprovalCard.tsx
frontend/components/LoopList.tsx

frontend/lib/api.ts
frontend/lib/supabase.ts
```

---

## 7.3 Inputs Consumed

Consumes:

```text
Loop Detail DTO

Approval objects

ActivityLog objects

Realtime database updates
```

---

## 7.4 Screens Required

### Screen 1 — Loop List

Show:

```text
active loops
status
goal
next unresolved node
```

---

### Screen 2 — Loop Detail

Primary view:

```text
Goal
Graph
Evidence
Activity
Actions
Approvals
```

---

### Screen 3 — Approval Interaction

Support:

```text
Approve
Edit
Reject
```

at minimum.

If edit is too costly:

```text
Approve
Reject
```

is sufficient.

---

## 7.5 Graph Visual Language

Suggested statuses:

```text
PENDING
ACTIVE
WAITING
BLOCKED
VERIFIED
FAILED
SUPERSEDED
```

Use consistent visual treatment.

Do not depend on color alone.

Include status text or icon.

---

## 7.6 Realtime Behavior

UI should visibly update when:

```text
node status changes

new evidence arrives

graph is repaired

action becomes verified

approval is created

loop completes
```

No manual refresh should be required during the demo.

---

## 7.7 Activity Feed

Important activities:

```text
Goal compiled
Calendar deadline created
Return label saved
Evidence detected
Node verified
Deadline missed
Follow-up proposed
Graph repaired
Action cancelled
Goal completed
```

This feed is important for explaining agent behavior to judges.

---

## 7.8 What Teammate D Must NOT Own

Do not:

```text
change backend state directly from browser

implement evidence verification

recompute graph dependencies

decide whether loop is complete

call Composio directly

implement separate frontend-only workflow logic
```

Frontend renders backend truth.

---

## 7.9 Definition of Done

- [ ] loop list renders
- [ ] graph renders
- [ ] node states are understandable
- [ ] activity feed renders
- [ ] evidence panel renders
- [ ] approval interaction works
- [ ] realtime graph updates work
- [ ] primary demo scenario looks polished
- [ ] fallback seeded data exists if external demo dependency fails

---

# 8. Shared Components

The following files are shared contracts and should not be casually modified by one teammate.

```text
backend/app/graph/schemas.py

backend/app/constants.py

supabase/schema.sql

API DTO definitions
```

Any breaking change requires:

```text
1. notify team
2. update contract doc
3. update dependent modules
```

---

# 9. "Do Not Cross" Rules

These boundaries are important.

## Intelligence must not execute tools

Bad:

```text
Compiler → Gmail directly
```

Correct:

```text
Compiler → Action
             ↓
          Executor
```

---

## Integrations must not reason about goals

Bad:

```text
Gmail integration:
"this looks like a refund so mark node complete"
```

Correct:

```text
Gmail integration
→ Event

Verifier
→ semantic decision
```

---

## Frontend must not own business state

Bad:

```text
user clicks complete
→ frontend marks node VERIFIED
```

Correct:

```text
user action
→ API
→ backend state transition
→ frontend receives update
```

---

## Runtime must not invent semantic meaning

Bad:

```text
if "refund" in message:
    complete refund node
```

Correct:

```text
Verifier returns PROVES
→ runtime applies valid transition
```

---

# 10. Team Integration Interfaces

The most important integration boundaries are:

---

## A → C

AI teammate exposes:

```text
compile()
verify()
replan()
```

Runtime teammate consumes those functions.

---

## B → C

Integration teammate exposes:

```text
execute_action()
verify_action()
normalize_webhook()
```

Runtime teammate consumes those functions.

---

## C → D

Runtime exposes REST/Realtime state.

Frontend consumes:

```text
GET /api/loops
GET /api/loops/{id}
GET /api/approvals
POST approve/reject
```

---

## D → C

Frontend only sends explicit user actions:

```text
create loop
approve action
reject action
manual demo event
```

---

# 11. Suggested Branch Structure

Use short-lived branches.

Recommended:

```text
main

feature/intelligence
feature/integrations
feature/runtime
feature/frontend
```

Optional smaller branches:

```text
feature/gmail
feature/slack
feature/compiler
```

but do not fragment too heavily during an 8-hour hackathon.

---

# 12. Merge Strategy

Recommended:

```text
main
→ always runnable
```

No one should keep a critical component isolated until late afternoon.

Suggested integration checkpoints:

```text
11:00 AM
First interface merge

1:00 PM
Happy-path integration

3:00 PM
Feature freeze integration

4:00 PM
Demo-only fixes
```

---

# 13. Git Rules

## Commit often

Prefer:

```text
small working commits
```

instead of:

```text
one giant end-of-day commit
```

---

## Pull before merge

Before integrating:

```text
git pull --rebase origin main
```

Resolve conflicts while component owners are available.

---

## Shared schema changes

Never silently change:

```text
enum names
field names
endpoint payloads
```

These are integration-breaking changes.

---

# 14. Mock Strategy

Each teammate should be able to work before other components are ready.

---

## Teammate A mocks

Use local:

```text
Event fixtures
Loop fixtures
Node fixtures
```

No app dependencies required.

---

## Teammate B mocks

Use static Actions:

```json
{
  "app": "google_calendar",
  "action_type": "CREATE_CALENDAR_EVENT"
}
```

No AI dependency required.

---

## Teammate C mocks

Use fake:

```text
CompiledGraph
VerifyEventResponse
ReplanResponse
ExecuteActionResponse
```

---

## Teammate D mocks

Use:

```text
seeded Loop Detail DTO
```

Frontend should look polished before backend integration.

---

# 15. Required Shared Fixtures

Create a shared folder:

```text
backend/tests/fixtures/
```

Suggested files:

```text
refund_goal.json
refund_confirmation_event.json
refund_delay_event.json

promise_goal.json
promise_reassignment_event.json
draft_presentation_event.json

renewal_goal.json
renewed_policy_event.json
landlord_requirement_change.json
```

Frontend may reuse fixture responses.

---

# 16. Integration Checkpoint 1 — 11:00 AM

Goal:

```text
All components speak the same contracts.
```

Required:

- [ ] Compiler returns a valid CompiledGraph
- [ ] Integrations can execute at least one test action
- [ ] Runtime persists a loop
- [ ] UI renders fixture graph
- [ ] shared schemas import cleanly

Do not worry yet about perfect demo flow.

---

# 17. Integration Checkpoint 2 — 1:00 PM

Goal:

```text
One end-to-end happy path works.
```

Recommended:

```text
Return email
↓
Compiler
↓
Persist
↓
Create Calendar deadline
↓
UI renders graph
```

At least one real external write should work.

---

# 18. Integration Checkpoint 3 — 3:00 PM

Goal:

```text
Primary demo complete.
```

Required:

- [ ] incoming event updates graph
- [ ] evidence verifies a node
- [ ] replanning works once
- [ ] approval works once
- [ ] action verification works
- [ ] UI updates live

At this point:

```text
FEATURE FREEZE
```

---

# 19. 3:00 PM Feature Freeze

After 3 PM:

Do:

```text
fix bugs
improve reliability
improve UI
improve demo script
add fallback fixtures
```

Do not:

```text
add new integration
change framework
change core schema
add new agent
rewrite orchestration
```

---

# 20. Demo Reliability Plan

The team should support two modes.

## Live Mode

Uses:

```text
real Gmail
real Calendar
real Slack
real Drive
```

---

## Controlled Demo Mode

Uses:

```text
manual event injection
seeded fixtures
real backend pipeline
```

Important:

Controlled mode must still pass through:

```text
Event Router
Verifier
Replanner
Runtime
UI
```

It should not be a fake animation.

This protects against:

```text
OAuth failure
webhook delay
rate limits
network issues
```

---

# 21. Suggested Work Distribution by Time

## 9:00–10:00

### A

```text
Compiler schema + initial prompt
```

### B

```text
Composio auth + Gmail/Calendar test
```

### C

```text
DB schema + shared Pydantic models
```

### D

```text
React Flow graph + fixture UI
```

---

## 10:00–11:00

### A

```text
Verifier
```

### B

```text
Slack + Drive
```

### C

```text
Create loop API + event routing
```

### D

```text
Loop detail + activity panel
```

---

## 11:00–1:00

Integrate first happy path.

Focus:

```text
Return / Refund
```

---

## 1:00–3:00

Add:

```text
replanning
approval
verification
promise scenario
renewal scenario
```

---

## 3:00–5:00

```text
stabilize
test
polish
rehearse
```

---

# 22. Ownership Conflict Resolution

If two teammates need the same file:

Prefer:

```text
one owner
one contributor
```

Example:

```text
schemas.py
Owner: C
Contributor: A
```

Avoid simultaneous independent edits to core shared files.

---

# 23. Communication Rhythm

During the hackathon:

Use short checkpoints approximately every hour.

Each person reports:

```text
DONE
BLOCKED
NEXT
```

Example:

```text
DONE:
Calendar create/read works.

BLOCKED:
Slack OAuth callback failing.

NEXT:
Drive upload + webhook normalization.
```

Keep discussions operational.

Do not spend 20 minutes debating naming unless it blocks integration.

---

# 24. Escalation Rules

If a component is blocked for more than approximately 20 minutes:

Ask:

```text
Can we simplify it?
Can we mock it?
Can we replace it?
Can we defer it?
```

Examples:

If Drive OAuth is blocking:

```text
use Gmail attachment + seeded Drive evidence
```

If Realtime is failing:

```text
poll API every 2 seconds
```

If Composio trigger is delayed:

```text
manual event injection endpoint
```

The architecture should survive component degradation.

---

# 25. Team-Wide Definition of Done

The whole team is done when:

### Architecture

- [ ] same generic engine supports all three scenarios
- [ ] no hard-coded scenario runner exists
- [ ] at least three external apps are genuinely connected

### Reasoning

- [ ] goal compiles into graph
- [ ] evidence updates existing graph
- [ ] replanning changes graph correctly

### Execution

- [ ] external action runs
- [ ] action is verified
- [ ] approval gating works

### Reliability

- [ ] duplicate events do not duplicate side effects
- [ ] stale actions can be cancelled
- [ ] graph state survives restart

### Product

- [ ] graph is visually understandable
- [ ] activity log explains behavior
- [ ] demo works without manual code intervention

---

# 26. Final Architecture Principle

Each teammate should think of their component as replaceable.

The system should work because the contracts are stable.

```text
Intelligence
does not care how Gmail works.

Integrations
do not care how the graph was reasoned about.

Frontend
does not care which LLM is used.

Runtime
does not care how the UI is styled.
```

That separation is the reason four people can build LoopGraph in parallel during a short hackathon.

The team should optimize for:

> **clear boundaries, stable contracts, fast integration, and a reliable demo.**
