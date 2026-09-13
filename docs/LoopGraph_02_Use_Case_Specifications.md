# LoopGraph — Use Case Specifications

## 1. Purpose

This document defines the expected behavior of LoopGraph for the three hackathon MVP use cases:

1. Return / Refund
2. Someone Promised Something
3. Paperwork / Renewal

These specifications are the team's source of truth for implementation and testing.

The goal is **not** to hard-code three workflows. Each scenario should be compiled into the same generic LoopGraph structures:

```text
Goal
↓
Outcome Nodes
↓
Dependencies
↓
Actions
↓
Evidence
↓
Verification
↓
Recovery / Replanning
↓
Completion
```

---

# 2. Shared Behavioral Rules

All three use cases follow the same runtime principles.

## 2.1 Every workflow begins with a goal

Example:

```text
"Make sure I receive my $129 refund."
```

The goal describes the desired final state, not the next action.

---

## 2.2 Nodes represent outcomes, not instructions

Bad node:

```text
Send email
```

Better node:

```text
Merchant confirms refund processing
```

Actions support outcome nodes.

---

## 2.3 Every important node must define completion evidence

Example:

```text
Outcome:
Receive $129 refund

Evidence:
Merchant confirmation that the refund was processed
```

The agent must not consider:

```text
Follow-up email sent
```

to be equivalent to:

```text
Refund received
```

---

## 2.4 New events can affect existing workflows

Every incoming event should be evaluated against active LoopGraphs.

Possible relationships:

```text
PROVES
CONTRADICTS
SUPERSEDES
TRIGGERS
BLOCKS
UNRELATED
```

---

## 2.5 Replanning modifies the existing graph

If new information changes the situation, LoopGraph should preserve useful existing state and modify only the affected nodes, edges, deadlines, or actions.

---

## 2.6 External communication requires approval

For hackathon MVP:

```text
Read/search apps             → autonomous
Save documents               → autonomous
Create internal state        → autonomous
Create low-risk reminders    → autonomous

Send Gmail message           → approval required
Send Slack message           → approval required
Delete/cancel external event → approval required
```

---

# 3. Use Case A — Return / Refund

## 3.1 User Problem

A user returns an item and must manually track multiple steps:

- return approval
- return deadline
- return label
- physical drop-off
- merchant receipt
- refund timing
- refund completion
- overdue follow-up

The real objective is not:

```text
Start return
```

It is:

```text
Receive the refund
```

---

## 3.2 Primary Demo Input

Example Gmail message:

> Your return for Order #A1298 has been approved. Please return the Sony WH-1000XM5 headphones by September 16, 2026. Your return label is attached. Once the item is received, your $129.00 refund will be processed within 5 business days.

---

## 3.3 Expected Goal

```text
Receive the $129.00 refund for Order #A1298.
```

---

## 3.4 Expected Outcome Graph

```text
GOAL
Receive $129 refund
        ↑
        │ DEPENDS_ON
        │
Refund processed
        ↑
        │ DEPENDS_ON
        │
Merchant receives returned item
        ↑
        │ DEPENDS_ON
        │
Return item before Sep 16
```

Optional supporting subnodes:

```text
Return item
├── Find/save return label
└── Complete physical drop-off
```

---

## 3.5 Expected Node Definitions

### Node A1 — Return Item

```text
Title:
Return Sony headphones

Status:
ACTIVE

Deadline:
Sep 16, 2026

Evidence Required:
Carrier drop-off or shipment confirmation

Possible Actions:
- locate return label
- save label to Drive
- create Calendar deadline
```

---

### Node A2 — Merchant Receives Return

```text
Title:
Merchant receives returned item

Status:
BLOCKED until A1 verified

Evidence Required:
Merchant confirmation or return-received email

Possible Actions:
- monitor Gmail
```

---

### Node A3 — Refund Processed

```text
Title:
Receive $129 refund

Status:
BLOCKED until A2 verified

Deadline:
5 business days after merchant receipt

Evidence Required:
Merchant confirms refund for $129.00 was processed

Possible Actions:
- monitor Gmail
- create refund checkpoint
- prepare follow-up if overdue
```

---

## 3.6 App Interactions

### Gmail

Read:

```text
return approval
merchant received confirmation
refund update
refund confirmation
```

Act:

```text
draft follow-up
send follow-up after approval
```

---

### Google Drive

Act:

```text
save return label
save drop-off receipt
```

Verify:

```text
file exists
expected filename/context matches
```

---

### Google Calendar

Act:

```text
create return deadline
create refund checkpoint
update refund deadline if changed
cancel obsolete checkpoint
```

Verify:

```text
event exists with correct deadline
```

---

## 3.7 Happy Path

```text
Return approval received
        ↓
LoopGraph compiled
        ↓
Return label saved to Drive
        ↓
Return deadline added to Calendar
        ↓
Drop-off confirmation arrives
        ↓
Return node VERIFIED
        ↓
Merchant received confirmation arrives
        ↓
Merchant receipt node VERIFIED
        ↓
Refund checkpoint calculated
        ↓
Refund confirmation arrives
        ↓
Refund node VERIFIED
        ↓
Future follow-ups cancelled
        ↓
GOAL COMPLETE
```

---

## 3.8 Recovery Scenario 1 — Refund Delay

Incoming Gmail:

> Due to processing delays, refunds are taking an additional 5 business days.

Expected behavior:

```text
Detect affected node:
Refund processed
        ↓
Relationship:
SUPERSEDES old deadline
        ↓
Update refund deadline
        ↓
Update Calendar checkpoint
        ↓
Cancel obsolete escalation
        ↓
Record reason in activity log
```

Expected graph change:

```text
Old:
Refund due Sep 21

New:
Refund due Sep 28
```

---

## 3.9 Recovery Scenario 2 — Refund Arrives Early

Incoming Gmail:

> Your $129.00 refund for Order #A1298 has been processed.

Expected behavior:

```text
Match evidence to refund node
        ↓
Verify order number
Verify amount
Verify merchant context
        ↓
Mark refund VERIFIED
        ↓
Cancel scheduled chase
        ↓
Cancel/remove obsolete Calendar reminder
        ↓
Mark root goal COMPLETE
```

Important negative behavior:

```text
DO NOT send scheduled follow-up.
```

---

## 3.10 Acceptance Criteria

- [ ] Return email is converted into a structured graph.
- [ ] Return deadline is extracted correctly.
- [ ] Refund amount is extracted correctly.
- [ ] Return label can be associated with the loop.
- [ ] Calendar deadline is created and read back.
- [ ] Drop-off evidence can verify Return Item.
- [ ] Merchant receipt evidence can unlock Refund node.
- [ ] Refund confirmation can complete the goal.
- [ ] Deadline-change message updates the graph.
- [ ] Future unnecessary follow-up is cancelled after early completion.
- [ ] Activity log explains why state changed.

---

# 4. Use Case B — Someone Promised Something

## 4.1 User Problem

People frequently promise to send:

- documents
- presentations
- contracts
- datasets
- reports
- photos
- invoices
- recommendation letters
- signatures

The receiver must remember:

```text
Who promised?
What?
By when?
Did it arrive?
Is it the correct version?
Do I need to follow up?
Did responsibility change?
```

LoopGraph should own that outcome.

---

## 4.2 Primary Demo Input

Example Slack message:

> Sarah: I'll send you the final client presentation by Friday.

---

## 4.3 Expected Goal

```text
Obtain the final client presentation.
```

---

## 4.4 Initial Outcome Graph

```text
GOAL
Obtain final presentation
        ↑
        │ DEPENDS_ON
        │
Receive valid final presentation
        ↑
        │ DEPENDS_ON
        │
Sarah delivers presentation by Friday
```

---

## 4.5 Expected Node Definitions

### Node B1 — Sarah Delivers Presentation

```text
Owner:
Sarah

Deadline:
Friday

Evidence Required:
Message or attachment indicating the final presentation was delivered

Possible Sources:
Slack
Gmail
Drive
```

---

### Node B2 — Receive Valid Final Presentation

```text
Evidence Required:
A presentation file that matches the requested artifact

Optional semantic checks:
- presentation/PPT/PDF
- correct project/client context
- "final" or latest version indicators
```

Possible Actions:

```text
save file to Drive
```

---

## 4.6 App Interactions

### Slack

Read:

```text
promise
status updates
reassignment
delivery message
```

Act:

```text
draft follow-up
send follow-up after approval
```

---

### Gmail

Read:

```text
document may arrive through email instead
```

---

### Google Drive

Act:

```text
save final document
```

Verify:

```text
file exists
file metadata/context matches expectation
```

---

### Google Calendar

Act:

```text
create Friday checkpoint
```

---

## 4.7 Happy Path

```text
Slack promise detected
        ↓
LoopGraph created
        ↓
Friday checkpoint created
        ↓
Sarah uploads/sends presentation Thursday
        ↓
Evidence detected
        ↓
File validated
        ↓
Saved to Drive
        ↓
Goal VERIFIED
        ↓
Friday checkpoint cancelled
```

---

## 4.8 Recovery Scenario 1 — Deadline Missed

Friday passes.

No evidence found.

Expected behavior:

```text
Scheduler emits DEADLINE_REACHED
        ↓
Verifier checks Slack/Gmail/Drive
        ↓
No valid evidence
        ↓
Node remains unresolved
        ↓
Replanner proposes follow-up
        ↓
User approval requested
        ↓
Slack follow-up sent
        ↓
Action read back / verified
```

Example draft:

> Hi Sarah, just checking in on the final client presentation you mentioned you'd send by Friday. Could you send it when you get a chance?

---

## 4.9 Recovery Scenario 2 — Responsibility Changes

Sarah replies:

> Mike actually has the final version. Please get it from him.

Expected behavior:

```text
Incoming Slack event
        ↓
Affected loop found
        ↓
Relationship:
SUPERSEDES current owner/dependency
        ↓
Graph modified
```

Before:

```text
Sarah
 ↓
Final presentation
```

After:

```text
Mike
 ↓
Final presentation
```

The original Sarah node should be:

```text
SUPERSEDED
```

or otherwise marked no longer active.

A new node should be created:

```text
Mike provides final presentation
```

Possible next action:

```text
Draft request to Mike
```

This must require approval before sending.

---

## 4.10 Recovery Scenario 3 — Wrong File Arrives

Suppose a file arrives named:

```text
client_presentation_draft_v2.pptx
```

Expected behavior:

```text
Attachment exists
        ↓
Semantic verifier checks evidence contract
        ↓
Does not confidently satisfy "final presentation"
        ↓
Do NOT close goal
        ↓
Request clarification or latest version
```

This is important for demonstrating:

```text
Evidence exists ≠ correct evidence exists
```

---

## 4.11 Acceptance Criteria

- [ ] Promise can be detected from Slack.
- [ ] Actor, artifact, and deadline are extracted.
- [ ] Checkpoint is created.
- [ ] Deadline miss triggers a recovery path.
- [ ] Follow-up requires approval.
- [ ] New owner can supersede old owner.
- [ ] Graph changes rather than starting from scratch.
- [ ] File can arrive through Slack/Gmail/Drive.
- [ ] Wrong/draft file does not automatically close the loop.
- [ ] Valid file completes the goal.
- [ ] Obsolete follow-up/reminder is cancelled.

---

# 5. Use Case C — Paperwork / Renewal

## 5.1 User Problem

Everyday administrative work often involves multiple hidden dependencies:

```text
renew something
receive updated document
find correct proof
send it to another party
wait for acknowledgement
```

Examples include:

- renter's insurance
- car registration
- school documents
- employee paperwork
- memberships
- lease documents
- warranties

The user often treats this as a single task even though it is a multi-step workflow.

---

## 5.2 Primary Demo Input

Example Gmail message from landlord:

> Your renter's insurance expires on September 25, 2026. Please email us your renewed policy before the current policy expires.

---

## 5.3 Expected Goal

```text
Landlord has valid proof of renewed renter's insurance before Sep 25.
```

---

## 5.4 Expected Outcome Graph

```text
GOAL
Landlord has valid insurance proof
        ↑
        │ DEPENDS_ON
        │
Landlord acknowledges receipt
        ↑
        │ DEPENDS_ON
        │
Send renewed policy
        ↑
        │ DEPENDS_ON
        │
Receive valid renewed policy
        ↑
        │ DEPENDS_ON
        │
Renew insurance
```

Supporting information:

```text
Current policy
Expiration: Sep 25
Landlord recipient
Required proof format
```

---

## 5.5 Expected Node Definitions

### Node C1 — Renew Insurance

```text
Deadline:
Before Sep 25

Evidence Required:
Renewal confirmation or renewed policy issued
```

For hackathon MVP, the agent does **not** autonomously purchase or renew insurance.

This node may require:

```text
human action
```

The system can track it and continue once evidence appears.

---

### Node C2 — Receive Valid Renewed Policy

```text
Evidence Required:
New insurance policy document

Required semantic properties:
- correct policy type
- valid future coverage period
- relevant user/property
```

Possible app:

```text
Gmail
Drive
```

---

### Node C3 — Send Renewed Policy

```text
Precondition:
C2 VERIFIED

Required Action:
Email correct document to landlord

Approval:
Required
```

Action verification:

```text
Read sent email
Confirm correct recipient
Confirm attachment exists
Confirm expected file attached
```

---

### Node C4 — Landlord Acknowledges Receipt

```text
Evidence Required:
Landlord confirms receipt/acceptance of renewed policy
```

Only after this node is verified should the overall workflow complete.

---

## 5.6 App Interactions

### Gmail

Read:

```text
landlord request
insurance renewal confirmation
policy delivery
landlord acknowledgement
```

Act:

```text
draft policy submission email
send after approval
```

---

### Google Drive

Read:

```text
current policy
renewed policy
```

Act:

```text
save renewed policy
organize evidence
```

---

### Google Calendar

Act:

```text
create Sep 25 expiration deadline
create submission checkpoint
```

---

## 5.7 Happy Path

```text
Landlord request received
        ↓
LoopGraph compiled
        ↓
Expiration checkpoint created
        ↓
Renewal confirmation arrives
        ↓
Renewed policy attached
        ↓
Policy validated
        ↓
Policy saved to Drive
        ↓
Submission email drafted
        ↓
User approves
        ↓
Email sent
        ↓
Sent state verified
        ↓
Landlord acknowledgement arrives
        ↓
Goal VERIFIED
        ↓
Remaining reminder cancelled
```

---

## 5.8 Recovery Scenario 1 — Missing Policy Document

Renewal email says:

> Your policy has been renewed successfully.

But no policy PDF is attached.

Expected behavior:

```text
Renewal confirmed
        ↓
C1 VERIFIED
        ↓
C2 still WAITING
        ↓
Do NOT send landlord email
        ↓
Replanner determines missing evidence
        ↓
Draft request / locate policy in Drive / wait for follow-up
```

This demonstrates dependency enforcement.

---

## 5.9 Recovery Scenario 2 — Wrong / Expired Document Found

Drive contains:

```text
renters_policy_2025.pdf
```

but new coverage should extend beyond Sep 25, 2026.

Expected behavior:

```text
Document found
        ↓
Semantic/document check
        ↓
Coverage period invalid
        ↓
Evidence rejected
        ↓
Node remains unresolved
```

The agent must not attach an old policy simply because the filename looks relevant.

---

## 5.10 Recovery Scenario 3 — Landlord Requests Different Proof

Landlord replies:

> Please send the declaration page rather than the full policy.

Expected behavior:

```text
Current evidence/action invalidated
        ↓
New requirement detected
        ↓
Graph repaired
        ↓
Find declaration page
        ↓
Draft corrected submission
        ↓
Approval
        ↓
Send
        ↓
Wait for acknowledgement
```

This demonstrates:

```text
Changing completion criteria
```

not just changing a deadline.

---

## 5.11 Acceptance Criteria

- [ ] Renewal request can create a graph.
- [ ] Expiration deadline is extracted.
- [ ] Current policy and renewed policy are distinguishable.
- [ ] Policy document can be saved to Drive.
- [ ] Wrong/old document does not satisfy evidence.
- [ ] Sending proof requires approval.
- [ ] Sent email is verified after execution.
- [ ] Landlord acknowledgement is required for final completion.
- [ ] Changed proof requirement can trigger graph repair.
- [ ] Obsolete reminders are cancelled after completion.

---

# 6. Cross-Use-Case Comparison

The three workflows must exercise different graph behaviors.

| Capability | Return / Refund | Promise | Paperwork / Renewal |
|---|---|---|---|
| Sequential dependencies | Yes | Light | Yes |
| Deadline | Yes | Yes | Yes |
| Human dependency | Merchant | Strong | Landlord/provider |
| File/document handling | Return label/receipt | Presentation | Insurance policy |
| Follow-up | Merchant | Person | Landlord/provider |
| Evidence verification | Refund confirmation | Correct final file | Valid renewed policy + acknowledgement |
| Reassignment | Rare | Core behavior | Possible |
| Changed deadline | Core recovery | Possible | Possible |
| Changed requirements | Possible | Possible | Core recovery |
| Cancellation of stale actions | Yes | Yes | Yes |
| Graph restructuring | Moderate | Strong | Strong |

The purpose is to demonstrate that the same LoopGraph engine supports different graph shapes and failure modes.

---

# 7. Common Event Examples

All scenario-specific external inputs should be normalized.

## Gmail Event

```json
{
  "source_app": "gmail",
  "event_type": "MESSAGE_RECEIVED",
  "external_id": "gmail_msg_123",
  "timestamp": "2026-09-13T11:42:00-04:00",
  "actor": "merchant@example.com",
  "content": "Your $129 refund has been processed.",
  "attachments": []
}
```

---

## Slack Event

```json
{
  "source_app": "slack",
  "event_type": "MESSAGE_RECEIVED",
  "external_id": "slack_msg_456",
  "timestamp": "2026-09-13T12:10:00-04:00",
  "actor": "Sarah",
  "content": "Mike actually has the final version.",
  "attachments": []
}
```

---

## Deadline Event

```json
{
  "source_app": "loopgraph",
  "event_type": "DEADLINE_REACHED",
  "external_id": "deadline_node_b1",
  "timestamp": "2026-09-18T17:00:00-04:00",
  "actor": "system",
  "content": "Deadline reached for Sarah to deliver final presentation."
}
```

---

# 8. Common Evidence Decision Format

Semantic verification should return structured output.

Example:

```json
{
  "affected_node_id": "refund_processed",
  "relationship": "PROVES",
  "confidence": 0.97,
  "reason": "The merchant explicitly confirms that the $129 refund for Order #A1298 was processed.",
  "evidence_fields": {
    "amount": 129.00,
    "order_id": "A1298"
  }
}
```

Another example:

```json
{
  "affected_node_id": "receive_final_presentation",
  "relationship": "INSUFFICIENT",
  "confidence": 0.92,
  "reason": "The received file is labeled as a draft and does not satisfy the request for the final presentation."
}
```

---

# 9. Common Recovery Decision Format

Replanning should express changes as operations against the current graph.

Example:

```json
{
  "reason": "Sarah stated that Mike owns the latest presentation.",
  "operations": [
    {
      "type": "SUPERSEDE_NODE",
      "node_id": "sarah_delivers_presentation"
    },
    {
      "type": "ADD_NODE",
      "node": {
        "id": "mike_delivers_presentation",
        "title": "Mike provides final presentation",
        "status": "ACTIVE"
      }
    },
    {
      "type": "ADD_EDGE",
      "source": "receive_final_presentation",
      "target": "mike_delivers_presentation",
      "relationship": "DEPENDS_ON"
    }
  ]
}
```

This approach is preferred over asking the model to regenerate the entire graph.

---

# 10. Demo Priority

If implementation time becomes constrained, prioritize in this order:

## Tier 1 — Must work live

### Return / Refund

Must demonstrate:

```text
compile
→ multi-app actions
→ evidence
→ changed condition
→ graph update
→ verified completion
```

---

## Tier 2 — Must demonstrate a distinct recovery pattern

### Someone Promised Something

Must demonstrate:

```text
promise
→ missed deadline
→ follow-up
→ responsibility changes
→ graph restructures
```

---

## Tier 3 — Must prove generality

### Paperwork / Renewal

At minimum demonstrate:

```text
renewal request
→ document dependency
→ send proof
→ acknowledgement required
```

This scenario may be partially pre-seeded if time is limited, but it must use the same graph/event/action schemas as the other scenarios.

---

# 11. Implementation Rule

The codebase must **not** contain scenario-specific orchestration such as:

```python
if scenario == "refund":
    run_refund_workflow()

if scenario == "promise":
    run_promise_workflow()

if scenario == "renewal":
    run_renewal_workflow()
```

Scenario-specific mock/test data is allowed.

The runtime itself should operate on generic:

```text
Loop
OutcomeNode
Edge
Event
Evidence
Action
```

The hackathon thesis depends on proving that these three workflows emerge from the same underlying engine.

---

# 12. Definition of Success

By the end of the hackathon, a judge should be able to see three apparently different everyday problems and understand:

> **LoopGraph does not know what a "refund workflow" or a "promise workflow" is. It knows goals, dependencies, evidence, actions, deadlines, and recovery.**

That is the core product and technical story this specification is designed to prove.
