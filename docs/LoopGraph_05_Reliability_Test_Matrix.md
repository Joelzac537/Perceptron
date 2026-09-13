# LoopGraph — Reliability & Test Matrix

## 1. Purpose

This document defines what "working reliably" means for LoopGraph.

The goal is not only to prove that the system can perform actions, but that it can:

- verify those actions
- distinguish actions from outcomes
- reject insufficient evidence
- avoid duplicate side effects
- recover from changed circumstances
- cancel stale actions
- preserve graph consistency
- fail safely when uncertain

The guiding principle is:

> **A successful API call is not the same as a successful real-world outcome.**

---

# 2. Reliability Model

LoopGraph reliability should be evaluated at five levels.

```text
1. Input correctness
2. Reasoning correctness
3. Action correctness
4. Outcome verification
5. Recovery correctness
```

A workflow is reliable only when all applicable levels succeed.

---

# 3. Core Reliability Principles

## 3.1 Actions must be verified

Example:

```text
Create Calendar event
        ↓
API reports success
        ↓
Read event back
        ↓
Compare expected and observed state
        ↓
Action VERIFIED
```

---

## 3.2 Outcomes require evidence

Example:

```text
Follow-up sent
```

does not imply:

```text
Refund received
```

---

## 3.3 Insufficient evidence must not close a node

Example:

```text
client_presentation_draft_v2.pptx
```

must not satisfy:

```text
Receive final client presentation
```

---

## 3.4 Duplicate inputs must not create duplicate outputs

If the same Gmail webhook is delivered twice:

```text
one Event
one graph update
one side effect
```

---

## 3.5 New information may invalidate prior plans

Example:

```text
Refund due Sep 21
```

later becomes:

```text
Refund delayed by 5 business days
```

The old follow-up should become stale.

---

## 3.6 Completion should clean up future work

When a loop completes:

```text
cancel obsolete reminder
cancel pending follow-up
stop deadline escalation
```

---

# 4. Test Severity Levels

Use three severity categories.

## P0 — Demo Critical

Failure breaks the central hackathon story.

Examples:

- graph does not compile
- wrong node marked complete
- duplicate message sent
- replanning breaks graph
- UI shows stale status

---

## P1 — Important

Failure degrades reliability but demo may continue.

Examples:

- activity message slightly wrong
- secondary metadata missing
- fallback verification unavailable

---

## P2 — Nice to Have

Polish-level behavior.

Examples:

- better filename normalization
- richer explanations
- improved UI copy

During the hackathon:

```text
Fix P0 first.
Fix P1 if time allows.
Ignore P2 after feature freeze.
```

---

# 5. Test Types

The MVP should include:

```text
Unit tests
Contract tests
Integration tests
End-to-end tests
Negative tests
Recovery tests
Idempotency tests
Approval tests
Demo fallback tests
```

---

# 6. Shared Test Environment

Recommended setup:

```text
Development Supabase project
Test Gmail account
Test Slack workspace/channel
Test Google Drive folder
Test Google Calendar
```

Do not use important personal accounts for destructive demo actions.

---

# 7. Shared Test Fixtures

Suggested fixture directory:

```text
backend/tests/fixtures/
```

Files:

```text
refund_return_approved.json
refund_delay.json
refund_confirmed.json
refund_wrong_order.json

promise_initial.json
promise_deadline_missed.json
promise_reassigned.json
promise_draft_file.json
promise_final_file.json

renewal_request.json
renewal_confirmation_without_pdf.json
renewed_policy_valid.json
renewed_policy_expired.json
landlord_acknowledgement.json
landlord_requirement_changed.json
```

---

# 8. Contract Tests

These tests ensure module interfaces remain compatible.

---

## 8.1 Compiler Contract

### Test C-01

Input:

```text
Valid refund email
```

Expected:

```text
CompiledGraph validates successfully
```

Priority:

```text
P0
```

---

### Test C-02

Compiler output contains edge referencing nonexistent node.

Expected:

```text
Graph validation rejects result
```

Priority:

```text
P0
```

---

### Test C-03

Compiler output missing root node.

Expected:

```text
Reject graph
Do not persist
Do not execute actions
```

Priority:

```text
P0
```

---

## 8.2 Verifier Contract

### Test V-01

Valid event.

Expected:

```text
VerifyEventResponse validates
```

---

### Test V-02

Relationship outside allowed enum.

Expected:

```text
Pydantic validation failure
Retry LLM once
No graph mutation
```

Priority:

```text
P0
```

---

## 8.3 Replanner Contract

### Test R-01

Valid graph operations.

Expected:

```text
Operations validate
```

---

### Test R-02

Operation targets nonexistent node.

Expected:

```text
Reject operation
Do not partially mutate graph
```

Priority:

```text
P0
```

---

# 9. Return / Refund Test Matrix

## RR-01 — Initial Compilation

Input:

> Return approved. Item must be returned by Sep 16. Refund $129 within five business days after receipt.

Expected:

```text
Goal:
Receive $129 refund

Nodes:
Return item
Merchant receives return
Receive refund

Dependencies valid
```

Priority:

```text
P0
```

---

## RR-02 — Calendar Deadline Creation

Action:

```text
Create return deadline
```

Expected:

```text
Calendar event created
Event read back
Title/date match
Action → VERIFIED
```

Priority:

```text
P0
```

---

## RR-03 — Duplicate Return Email

Input:

Same webhook delivered twice.

Expected:

```text
Only one Event persisted
Only one Calendar event
Only one loop
```

Priority:

```text
P0
```

---

## RR-04 — Drop-Off Evidence

Input:

Carrier/drop-off confirmation.

Expected:

```text
Return Item → VERIFIED
Merchant Receives → may become ACTIVE/WAITING
```

Priority:

```text
P0
```

---

## RR-05 — Wrong Order Refund

Input:

> Your $89 refund for Order #B551 was processed.

Active loop expects:

```text
$129
Order #A1298
```

Expected:

```text
UNRELATED or INSUFFICIENT

Do NOT verify refund node
```

Priority:

```text
P0
```

---

## RR-06 — Correct Refund

Input:

> Your $129 refund for Order #A1298 was processed.

Expected:

```text
Refund node → VERIFIED
Root goal → COMPLETED
```

Priority:

```text
P0
```

---

## RR-07 — Refund Delay

Input:

> Refund processing is delayed by five business days.

Expected:

```text
old deadline superseded
new deadline calculated
calendar checkpoint updated
old escalation cancelled
activity log written
```

Priority:

```text
P0
```

---

## RR-08 — Refund Arrives Before Scheduled Chase

Setup:

```text
Follow-up scheduled tomorrow
```

Input:

```text
Valid refund confirmation today
```

Expected:

```text
refund VERIFIED
follow-up CANCELLED
calendar checkpoint removed/cancelled
no message sent tomorrow
```

Priority:

```text
P0
```

---

## RR-09 — Calendar API Reports Success but State Wrong

Simulate:

```text
create returns success
read-back has wrong date
```

Expected:

```text
Action not VERIFIED
ACTION_VERIFICATION_FAILED
retry or surface failure
```

Priority:

```text
P0
```

---

# 10. Promise Test Matrix

## PR-01 — Promise Extraction

Input:

> Sarah: I'll send you the final client presentation by Friday.

Expected:

```text
owner = Sarah
artifact = final client presentation
deadline = Friday
```

Priority:

```text
P0
```

---

## PR-02 — Deadline Missed

Setup:

```text
Friday arrives
No valid evidence
```

Expected:

```text
DEADLINE_REACHED emitted once
node remains unresolved
follow-up proposed
approval created
```

Priority:

```text
P0
```

---

## PR-03 — Approval Required

Follow-up is proposed.

Expected:

```text
No Slack send before approval
```

Priority:

```text
P0
```

---

## PR-04 — Approve Follow-Up

Input:

```text
APPROVE
```

Expected:

```text
Slack message sent
message read back
Action → VERIFIED
```

Priority:

```text
P0
```

---

## PR-05 — Reject Follow-Up

Input:

```text
REJECT
```

Expected:

```text
Action → CANCELLED
No Slack message sent
```

Priority:

```text
P0
```

---

## PR-06 — Responsibility Changes

Input:

> Sarah: Mike actually has the final version.

Expected:

```text
Sarah node → SUPERSEDED
Mike node added
dependency updated
request-to-Mike action proposed
existing final-document goal preserved
```

Priority:

```text
P0
```

---

## PR-07 — Duplicate Reassignment Event

Input:

Same Sarah→Mike event delivered twice.

Expected:

```text
One Mike node
One reassignment
No duplicate request action
```

Priority:

```text
P0
```

---

## PR-08 — Draft File Arrives

Input:

```text
client_presentation_draft_v2.pptx
```

Expected:

```text
INSUFFICIENT
Goal remains open
```

Priority:

```text
P0
```

---

## PR-09 — Correct Final File Arrives

Input:

```text
client_presentation_final.pptx
```

Expected:

```text
evidence accepted
file optionally saved to Drive
final presentation node VERIFIED
goal COMPLETED
future checkpoint cancelled
```

Priority:

```text
P0
```

---

## PR-10 — File Exists but Drive Save Fails

Expected:

```text
document outcome may be semantically satisfied
Drive action marked FAILED
loop does not falsely claim Drive action verified
activity log shows partial success
```

Priority:

```text
P1
```

---

# 11. Paperwork / Renewal Test Matrix

## PW-01 — Renewal Request Compilation

Input:

> Your renter's insurance expires Sep 25. Please send renewed proof before expiration.

Expected:

```text
expiration extracted
goal = landlord has valid proof
graph includes renewed document + submission + acknowledgement
```

Priority:

```text
P0
```

---

## PW-02 — Renewal Confirmed but No PDF

Input:

> Your insurance renewal is complete.

No attachment.

Expected:

```text
Renew Insurance → VERIFIED
Receive Valid Policy → still WAITING
Do NOT draft/send landlord proof yet
```

Priority:

```text
P0
```

---

## PW-03 — Old Policy Found

Drive contains:

```text
renters_policy_2025.pdf
```

Expected:

```text
document evidence rejected
node remains unresolved
```

Priority:

```text
P0
```

---

## PW-04 — Valid Renewed Policy

Input:

New policy with valid future coverage.

Expected:

```text
Receive Valid Policy → VERIFIED
Send Policy → ACTIVE
```

Priority:

```text
P0
```

---

## PW-05 — Submission Requires Approval

Expected:

```text
Email draft created
Approval PENDING
No message sent before approval
```

Priority:

```text
P0
```

---

## PW-06 — Correct Submission

After approval:

Expected:

```text
recipient correct
attachment exists
correct policy attached
sent message read back
Send Policy → VERIFIED
```

Priority:

```text
P0
```

---

## PW-07 — Missing Attachment

Simulate:

```text
email sent without policy attachment
```

Expected:

```text
Action verification fails
Send Policy node not verified
Recovery required
```

Priority:

```text
P0
```

---

## PW-08 — Landlord Acknowledgement

Input:

> Thanks, we've received your renewed insurance.

Expected:

```text
acknowledgement node VERIFIED
root goal COMPLETED
remaining reminders cancelled
```

Priority:

```text
P0
```

---

## PW-09 — Requirement Changes

Input:

> Please send the declaration page instead of the full policy.

Expected:

```text
previous evidence requirement superseded/updated
new document requirement added
old pending submission cancelled if needed
graph repaired
```

Priority:

```text
P0
```

---

## PW-10 — Wrong Recipient

Action is configured with unexpected recipient.

Expected:

```text
pre-execution validation blocks send
or post-send verification flags critical failure
```

Priority:

```text
P0
```

Preferred:

```text
block before send
```

---

# 12. Event Routing Tests

## ER-01 — Strong Identifier Match

Refund event includes:

```text
Order #A1298
```

Expected:

```text
route to refund loop with high confidence
```

Priority:

```text
P0
```

---

## ER-02 — Semantic Match

Promise event:

```text
Mike sent the final presentation
```

No direct loop ID.

Expected:

```text
route to presentation loop
```

Priority:

```text
P1
```

---

## ER-03 — Unrelated Event

Input:

```text
Weekly newsletter
```

Expected:

```text
no loop match
no graph mutation
```

Priority:

```text
P0
```

---

## ER-04 — Ambiguous Match

Two active loops mention:

```text
insurance
```

Event lacks identifying details.

Expected:

```text
do not auto-mutate both
flag ambiguity / low confidence
```

Priority:

```text
P1
```

---

# 13. Idempotency Tests

## ID-01 — Duplicate Webhook

Same:

```text
source_app + external_id
```

Expected:

```text
only first event processed
```

Priority:

```text
P0
```

---

## ID-02 — Duplicate Action

Same idempotency key:

```text
loop_refund_001:return_deadline
```

Expected:

```text
second execution skipped
```

Priority:

```text
P0
```

---

## ID-03 — Duplicate Replanner Output

Replanner suggests existing Mike node again.

Expected:

```text
graph validator prevents duplicate semantic node/edge
```

Priority:

```text
P0
```

---

## ID-04 — Scheduler Runs Twice

Same overdue node checked twice.

Expected:

```text
only one active DEADLINE_REACHED event for same due occurrence
```

Priority:

```text
P0
```

---

# 14. State Transition Tests

## ST-01

```text
PENDING → ACTIVE
```

Allowed.

---

## ST-02

```text
WAITING → VERIFIED
```

Allowed when evidence satisfied.

---

## ST-03

```text
VERIFIED → ACTIVE
```

Without contradiction/repair.

Expected:

```text
reject transition
```

Priority:

```text
P0
```

---

## ST-04

Prior evidence invalidated by explicit contradiction.

Expected:

```text
record repair reason
allow controlled reopen
```

Priority:

```text
P1
```

---

# 15. Approval Tests

## AP-01 — Medium-Risk Action

Action:

```text
SEND_EMAIL
```

Expected:

```text
AWAITING_APPROVAL
```

---

## AP-02 — Low-Risk Action

Action:

```text
CREATE_CALENDAR_EVENT
```

Expected:

```text
may execute autonomously
```

---

## AP-03 — High-Risk Action

Action:

```text
MAKE_PAYMENT
```

Expected:

```text
unsupported
do not execute
```

Priority:

```text
P0
```

---

## AP-04 — Approval Replay

Same approval endpoint called twice.

Expected:

```text
one execution
second call is idempotent/no duplicate side effect
```

Priority:

```text
P0
```

---

# 16. LLM Failure Tests

## LLM-01 — Invalid JSON

Expected:

```text
Pydantic validation fails
retry once
```

---

## LLM-02 — Invalid JSON Twice

Expected:

```text
LLM_OUTPUT_INVALID
no external side effects
```

Priority:

```text
P0
```

---

## LLM-03 — Hallucinated Node Reference

Expected:

```text
graph validator rejects
```

Priority:

```text
P0
```

---

## LLM-04 — Overconfident Evidence

Model says:

```text
PROVES
```

but required structured fields mismatch.

Expected:

```text
business-rule validator rejects verification
```

Priority:

```text
P0
```

Example:

```text
expected order A1298
observed B551
```

---

# 17. Integration Failure Tests

## IN-01 — Gmail Unavailable

Expected:

```text
integration error recorded
no graph corruption
retry/recovery possible
```

Priority:

```text
P1
```

---

## IN-02 — Slack Send Fails

Expected:

```text
Action → FAILED
Node remains unresolved
user sees failure
```

Priority:

```text
P0
```

---

## IN-03 — Calendar Create Fails

Expected:

```text
Action → FAILED
loop still persists
runtime can retry
```

Priority:

```text
P1
```

---

## IN-04 — Drive Upload Succeeds, Verification Fails

Expected:

```text
Action remains EXECUTED but not VERIFIED
```

This distinction must remain visible.

Priority:

```text
P0
```

---

# 18. Recovery Tests

## RC-01 — Deadline Moves

Expected:

```text
new deadline applied
old scheduled escalation cancelled
```

---

## RC-02 — Owner Changes

Expected:

```text
old owner superseded
new owner node created
```

---

## RC-03 — Evidence Requirement Changes

Expected:

```text
old requirement updated/superseded
new requirement active
```

---

## RC-04 — Desired Outcome Achieved Early

Expected:

```text
future actions cancelled
```

---

## RC-05 — Action Fails

Expected:

```text
recovery strategy triggered or failure surfaced
do not mark outcome complete
```

---

# 19. Negative Behavior Matrix

These tests are as important as happy paths.

| Scenario | System Must NOT Do |
|---|---|
| Refund follow-up sent | Mark refund complete |
| Draft presentation arrives | Mark final presentation complete |
| Old insurance policy found | Treat renewal as complete |
| Merchant delay received | Send follow-up on old deadline |
| Correct refund arrives early | Send already scheduled chase |
| Slack event delivered twice | Send two follow-ups |
| Email draft created | Claim message was sent |
| Calendar create API returns 200 | Mark verified without read-back |
| Landlord submission email lacks attachment | Mark proof submitted |
| Ambiguous event matches two loops | Mutate both automatically |
| LLM returns invalid structure | Execute side effects anyway |

---

# 20. End-to-End Primary Demo Test

This is the most important single test.

## E2E-RR-01

### Step 1

Inject return approval.

Expected:

```text
Loop created
Graph rendered
Calendar deadline created + verified
```

### Step 2

Inject drop-off evidence.

Expected:

```text
Return node VERIFIED
Next node activates
```

### Step 3

Inject merchant received evidence.

Expected:

```text
Merchant node VERIFIED
Refund checkpoint created
```

### Step 4

Inject delay message.

Expected:

```text
Refund deadline changes
Calendar changes
Activity log explains repair
```

### Step 5

Inject refund confirmation.

Expected:

```text
Refund VERIFIED
Loop COMPLETED
Future chase cancelled
UI updates live
```

Priority:

```text
P0 — absolute demo blocker
```

---

# 21. Secondary Demo Test — Promise

## E2E-PR-01

```text
Promise detected
↓
deadline reached
↓
follow-up proposed
↓
approval
↓
follow-up sent + verified
↓
owner changes to Mike
↓
graph repaired
↓
final file arrives
↓
goal complete
```

Priority:

```text
P0 for demo generality
```

---

# 22. Secondary Demo Test — Renewal

## E2E-PW-01

```text
renewal request
↓
deadline created
↓
renewal confirmed without PDF
↓
node remains waiting
↓
valid policy arrives
↓
submission proposed
↓
approval
↓
send + verify
↓
landlord acknowledges
↓
goal complete
```

Priority:

```text
P1 if time-constrained
```

---

# 23. Realtime UI Tests

## UI-01

Backend changes node status.

Expected:

```text
UI reflects new status without manual refresh
```

Priority:

```text
P0
```

---

## UI-02

Graph repaired.

Expected:

```text
new node appears
superseded node remains visible or identifiable
activity entry explains repair
```

Priority:

```text
P0
```

---

## UI-03

Approval created.

Expected:

```text
approval UI appears
```

Priority:

```text
P0
```

---

## UI-04

Loop completes.

Expected:

```text
root status visibly COMPLETED
stale actions no longer shown as pending
```

Priority:

```text
P0
```

---

# 24. Persistence Tests

## DB-01 — Restart

Create active loop.

Restart backend.

Expected:

```text
loop still exists
graph state preserved
pending actions preserved
```

Priority:

```text
P0
```

---

## DB-02 — Activity History

After graph repair:

Expected:

```text
original event
original state
repair activity
new state
```

remain queryable.

Priority:

```text
P1
```

---

# 25. Demo Fallback Tests

The demo should survive third-party issues.

---

## FB-01 — Gmail Trigger Delayed

Fallback:

```text
manual POST /api/events
```

Expected:

```text
same production event pipeline
```

Priority:

```text
P0
```

---

## FB-02 — Supabase Realtime Fails

Fallback:

```text
frontend polls GET /api/loops/{id}
every 1–2 seconds
```

Priority:

```text
P1
```

---

## FB-03 — Slack API Fails During Demo

Fallback:

```text
show approval + attempted execution
inject verified follow-up event if needed
```

But clearly distinguish:

```text
live action failed
controlled event used
```

Do not fake success silently.

---

# 26. Manual Pre-Demo Checklist

Before presenting:

### Accounts

- [ ] Gmail connected
- [ ] Slack connected
- [ ] Calendar connected
- [ ] Drive connected

### Data

- [ ] test inbox clean enough to search
- [ ] expected Slack channel exists
- [ ] Drive demo folder exists
- [ ] Calendar demo events cleaned up

### Backend

- [ ] server healthy
- [ ] Supabase reachable
- [ ] scheduler running
- [ ] webhook endpoint reachable
- [ ] manual event endpoint works

### Agents

- [ ] compiler tested
- [ ] verifier tested
- [ ] replanner tested

### UI

- [ ] graph loads
- [ ] activity feed loads
- [ ] approval panel works
- [ ] realtime/polling works

### Demo

- [ ] primary fixture sequence ready
- [ ] fallback events ready
- [ ] reset script available

---

# 27. Reset Strategy

Create a simple demo reset mechanism.

Example:

```text
POST /api/dev/reset-demo
```

or a script:

```bash
python scripts/reset_demo.py
```

It should:

```text
delete demo loops
delete demo actions
delete demo evidence
clear demo Calendar events if safe
reset seeded fixture state
```

Do not rely on manually cleaning state five minutes before presentation.

---

# 28. Test Ownership

Recommended:

## Teammate A

Owns:

```text
Compiler tests
Verifier tests
Replanner semantic tests
```

---

## Teammate B

Owns:

```text
Integration tests
Action verification
Webhook normalization
```

---

## Teammate C

Owns:

```text
Idempotency
State transitions
Persistence
Scheduler
End-to-end backend tests
```

---

## Teammate D

Owns:

```text
UI state tests
Realtime behavior
Demo fallback validation
```

---

# 29. Minimum Test Set Before Feature Freeze

At 3 PM, the following must pass.

## P0 Required

- [ ] RR-01 Initial compilation
- [ ] RR-02 Calendar action verified
- [ ] RR-05 Wrong refund rejected
- [ ] RR-06 Correct refund accepted
- [ ] RR-07 Delay replans deadline
- [ ] RR-08 Early completion cancels stale chase
- [ ] PR-01 Promise extraction
- [ ] PR-02 Deadline generates recovery
- [ ] PR-03 No send before approval
- [ ] PR-06 Owner reassignment
- [ ] PR-08 Draft file rejected
- [ ] PW-02 Renewal without document stays open
- [ ] PW-03 Old policy rejected
- [ ] PW-05 Submission requires approval
- [ ] ID-01 Duplicate webhook safe
- [ ] ID-02 Duplicate action safe
- [ ] LLM-02 Invalid output causes no side effects
- [ ] UI-01 Graph updates automatically
- [ ] E2E-RR-01 Primary demo works

If these pass, LoopGraph has a strong reliable demo even if some secondary features are incomplete.

---

# 30. Final Reliability Standard

LoopGraph should never claim success merely because it:

```text
sent
scheduled
saved
requested
attempted
```

The system should claim completion only when it can answer:

```text
What outcome were we trying to achieve?

What evidence proves it happened?

Does the observed state satisfy that evidence contract?

Did anything later invalidate that evidence?

Are any stale actions still scheduled?
```

If those questions can be answered correctly, the loop may be considered complete.

That is the reliability standard the hackathon MVP should demonstrate.
