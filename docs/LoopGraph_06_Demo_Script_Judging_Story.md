# LoopGraph — Demo Script & Judging Story

## 1. Purpose

This document defines how LoopGraph should be presented during the hackathon demo.

The objective is to make judges understand three things quickly:

1. **LoopGraph owns outcomes, not just actions.**
2. **The same engine handles multiple everyday workflows across apps.**
3. **The system verifies and repairs its own plans instead of blindly executing automations.**

The demo should prioritize clarity over feature count.

---

# 2. Core Judging Story

The opening message should be simple:

> **Most automations stop after performing an action. LoopGraph keeps going until the real-world outcome is actually verified.**

Examples:

```text
Sending a return request ≠ receiving the refund.

Sending a follow-up ≠ receiving the promised document.

Sending an insurance policy ≠ knowing the landlord accepted it.
```

LoopGraph solves the gap between:

```text
"I did something."
```

and:

```text
"The outcome actually happened."
```

---

# 3. One-Sentence Product Pitch

Use this exact or near-exact framing:

> **LoopGraph turns unfinished everyday obligations into self-healing outcome graphs that act across your apps, verify completion with evidence, and repair themselves when reality changes.**

Alternative shorter version:

> **LoopGraph owns the outcome, not just the action.**

---

# 4. What the Demo Must Prove

The demo should visibly prove:

```text
Goal understanding
↓
Outcome graph generation
↓
Multi-app execution
↓
Future event observation
↓
Evidence verification
↓
Changed-condition detection
↓
Graph repair
↓
Verified completion
```

The judges should not need to infer these capabilities.

Each should be visible.

---

# 5. Recommended Demo Duration

Target:

```text
2.5–3.5 minutes
```

Suggested timing:

```text
0:00–0:25  Problem + pitch

0:25–1:50  Primary Return / Refund workflow

1:50–2:30  Promise workflow

2:30–3:00  Renewal workflow

3:00–3:20  Architecture / reliability close
```

If the allowed presentation is shorter:

Prioritize:

```text
Return / Refund
+
one Promise replanning moment
+
closing architecture slide
```

---

# 6. Demo Roles

Recommended four-person presentation roles:

## Person A — Presenter / Story

Handles:

```text
opening
problem framing
transitions
closing
```

Should not spend time typing.

---

## Person B — App Operator

Handles:

```text
Gmail
Slack
Drive
Calendar
```

Triggers live external events if needed.

---

## Person C — Backend / Reliability Operator

Handles:

```text
manual event fallback
logs
reset
backend state
```

Should remain mostly invisible unless something fails.

---

## Person D — Product UI Operator

Controls:

```text
LoopGraph dashboard
graph
activity feed
approvals
```

The UI should remain the main screen most of the time.

---

# 7. Primary Demo — Return / Refund

This is the main live workflow.

## 7.1 Initial State

Start with LoopGraph dashboard.

Show:

```text
No active loop
```

Then introduce the scenario:

> “I bought headphones online, returned them, and now I need to make sure I actually get my $129 back.”

---

## 7.2 Trigger Event

Inject or receive Gmail:

> Your return for Order #A1298 has been approved. Please return the Sony headphones by September 16. Your return label is attached. Once received, your $129 refund will be processed within 5 business days.

---

## 7.3 What the Presenter Says

> “LoopGraph doesn't create one reminder. It compiles the real outcome into dependent states.”

---

## 7.4 What the UI Should Show

Graph appears:

```text
Receive $129 refund
        ↑
Refund processed
        ↑
Merchant receives return
        ↑
Return item
```

Supporting details:

```text
Deadline: Sep 16
Amount: $129
Order: A1298
```

---

## 7.5 Show Multi-App Actions

Activity feed:

```text
✓ Return obligation detected

✓ Outcome graph generated

✓ Return deadline created in Calendar

✓ Return label saved to Drive
```

If possible, briefly open:

```text
Calendar
```

to show the real deadline.

Then return to LoopGraph.

---

# 8. Evidence Transition

Trigger:

```text
carrier/drop-off confirmation
```

The graph should update:

```text
Return Item
✓ VERIFIED
```

Then:

```text
Merchant Receives Return
● WAITING
```

Presenter:

> “LoopGraph distinguishes actions from outcomes. Creating the return deadline did not complete the return. It waited for evidence.”

---

# 9. Merchant Receipt

Trigger Gmail:

> We've received your return for Order #A1298.

Expected:

```text
Merchant Receives Return
✓ VERIFIED

Receive Refund
● WAITING

Refund checkpoint created
```

Activity:

```text
✓ Merchant receipt verified

✓ Refund deadline calculated
```

---

# 10. The Important Demo Moment — Reality Changes

Trigger Gmail:

> Due to processing delays, refunds are taking an additional 5 business days.

This is the key demo moment.

The UI should visibly change:

```text
OLD:
Refund due Sep 21

NEW:
Refund due Sep 28
```

Activity:

```text
↻ Graph repaired

Refund deadline moved from Sep 21 to Sep 28.

Old escalation cancelled.

Calendar checkpoint updated.
```

Presenter:

> “Traditional automation keeps following the old plan. LoopGraph reconciles new information against the graph and repairs the workflow.”

This is the point where the system should feel different from Zapier-style automation.

---

# 11. Completion

Trigger Gmail:

> Your $129.00 refund for Order #A1298 has been processed.

Expected UI:

```text
Receive Refund
✓ VERIFIED

GOAL
✓ COMPLETED
```

Activity:

```text
✓ Refund evidence matched

✓ $129 amount verified

✓ Order A1298 verified

✓ Future follow-up cancelled

✓ Calendar checkpoint removed

✓ Loop completed
```

Presenter:

> “It doesn't stop because it sent a follow-up. It stops because it found evidence that the goal actually happened.”

---

# 12. Secondary Demo — Someone Promised Something

This scenario should be shorter.

Introduce:

> “The same engine works for human commitments.”

---

## 12.1 Initial Slack Message

Show:

> Sarah: I'll send you the final client presentation by Friday.

Expected graph:

```text
Obtain final presentation
        ↑
Receive valid final file
        ↑
Sarah delivers by Friday
```

Calendar checkpoint appears.

---

# 13. Missed Deadline

Inject:

```text
DEADLINE_REACHED
```

or let controlled demo mode trigger it.

Expected:

```text
No valid evidence found.

Follow-up proposed.
```

Approval card appears:

```text
Send Slack follow-up?

[Approve]
[Reject]
```

Presenter:

> “Communication is medium-risk, so LoopGraph pauses for approval instead of impersonating the user automatically.”

Approve.

Activity:

```text
✓ Follow-up sent
✓ Slack message verified
```

---

# 14. Responsibility Changes

Trigger Slack:

> Sarah: Mike actually has the final version. Please get it from him.

Expected visual:

Before:

```text
Sarah
 ↓
Final Presentation
```

After:

```text
Sarah
SUPERSEDED

Mike
 ↓
Final Presentation
```

Activity:

```text
↻ Graph repaired

Responsibility moved from Sarah to Mike.
```

Presenter:

> “LoopGraph doesn't restart the workflow. It preserves the goal and replaces only the invalid dependency.”

This is the strongest moment in the Promise scenario.

---

# 15. Optional File Verification Moment

Inject:

```text
client_presentation_draft_v2.pptx
```

Expected:

```text
Evidence rejected:
Draft does not satisfy "final presentation".
```

Then inject:

```text
client_presentation_final.pptx
```

Expected:

```text
✓ Valid evidence

✓ Goal complete
```

This strongly demonstrates semantic verification.

If time is limited, skip the draft file and show only owner reassignment.

---

# 16. Secondary Demo — Paperwork / Renewal

This scenario should be mostly pre-seeded and fast.

Presenter:

> “And it works for administrative paperwork, where a single 'task' is really a chain of dependent outcomes.”

---

# 17. Renewal Scenario

Show Gmail:

> Your renter's insurance expires September 25. Please send renewed proof before expiration.

Graph:

```text
Landlord has valid proof
        ↑
Landlord acknowledges
        ↑
Send renewed policy
        ↑
Receive valid renewed policy
        ↑
Renew insurance
```

---

# 18. Show Dependency Enforcement

Inject:

> Your insurance renewal is complete.

But no policy PDF.

Expected:

```text
Renew Insurance
✓ VERIFIED

Receive Valid Policy
● WAITING

Send Policy
BLOCKED
```

Presenter:

> “The system refuses to skip dependencies. Renewal confirmation is not the same as having the document required by the landlord.”

This is enough to establish generality.

---

# 19. Optional Changed-Requirement Moment

If time allows, inject:

> Please send the declaration page instead of the full policy.

Expected:

```text
Old evidence requirement superseded
New requirement created
Submission plan updated
```

This further demonstrates repair.

---

# 20. Closing Technical Story

After the workflows, show a simple architecture screen or slide.

Use:

```text
External Apps
Gmail / Slack / Drive / Calendar
        ↓
Events
        ↓
Outcome Graph
        ↓
Evidence Verifier
        ↓
Replanner
        ↓
Actions
        ↓
Read-After-Write Verification
```

Then say:

> “The important part is that these are not three hard-coded workflows. The same runtime only understands goals, dependencies, evidence, actions, deadlines, and recovery.”

---

# 21. Reliability Message

Explicitly mention:

> **We use APIs to verify state and AI to verify meaning.**

Example:

```text
Calendar created?
→ verify with Calendar API

Does this email prove the refund happened?
→ semantic verifier
```

Then:

> “An API returning 200 doesn't mean the user outcome succeeded.”

This is a strong technical line.

---

# 22. Novelty / Differentiation Message

Do not say:

```text
"Nobody has ever tracked commitments."
```

Instead say:

> “Existing automation tools typically execute predefined steps. LoopGraph continuously reconciles a persistent outcome graph with new events, verifies semantic evidence, and repairs dependent workflows when reality changes.”

That is a safer and stronger claim.

---

# 23. What NOT to Spend Demo Time On

Do not show:

```text
database tables
long code
OAuth setup
Composio dashboard
package installation
LLM prompt text
JSON schemas
Supabase console
```

Unless a judge asks.

The demo should stay focused on:

```text
user outcome
graph behavior
external actions
evidence
repair
completion
```

---

# 24. Suggested Opening Script

Use something like:

> “Every day, we create automations and reminders for things we need to get done. But the automation usually stops after taking an action. If I send a refund request, I still have to remember whether the money actually arrived. If a coworker promises me a file, I still have to remember to chase them. LoopGraph closes that gap. It turns unfinished obligations into outcome graphs and keeps working across your apps until the result is actually verified.”

Then:

> “Let me show you.”

---

# 25. Suggested Closing Script

> “What you saw were three different workflows: a refund, a human promise, and paperwork. None of them are hard-coded. LoopGraph represents each as the same primitives: goals, dependencies, evidence, deadlines, actions, and recovery. It can act across multiple apps, but more importantly, it knows when the outcome actually happened—and when the plan needs to change.”

Then finish with:

> **“LoopGraph owns the outcome, not just the action.”**

---

# 26. Demo Screen Layout

Recommended primary screen:

```text
┌──────────────────────────────────────────┐
│ LoopGraph                Gmail ● Slack ● │
│                          Drive ● Cal ●    │
├──────────────────────────────────────────┤
│ Goal: Receive $129 refund                │
├──────────────────────────────────────────┤
│                                          │
│              Outcome Graph               │
│                                          │
│         [Return Item ✓]                  │
│                ↓                         │
│    [Merchant Received ✓]                 │
│                ↓                         │
│       [Refund Waiting ●]                 │
│                                          │
├─────────────────────┬────────────────────┤
│ Evidence            │ Activity           │
│                     │                    │
│ ✓ Drop-off receipt  │ 10:31 Graph made  │
│ ✓ Merchant email    │ 10:32 Cal created │
│ ○ Refund evidence   │ 10:44 Delay found │
└─────────────────────┴────────────────────┘
```

Keep the graph visually dominant.

---

# 27. Visual States

Use consistent visual semantics.

Recommended:

```text
✓ VERIFIED

● ACTIVE / WAITING

! BLOCKED

↻ SUPERSEDED / REPAIRED

× FAILED
```

Do not rely only on color.

---

# 28. Activity Feed Copy

Keep activity messages human-readable.

Good:

```text
Refund deadline moved to Sep 28 because the merchant announced a 5-day delay.
```

Bad:

```text
UPDATE_NODE operation applied.
```

Technical details may appear in expandable metadata.

---

# 29. Approval UI

Approval card should display:

```text
Why action is needed

What will happen

Which app

Exact message/content

Approve / Reject
```

Example:

```text
Follow up with Sarah?

Reason:
Presentation deadline passed and no valid file was found.

Message:
"Hi Sarah, just checking in..."

[Approve] [Reject]
```

This reinforces safe autonomy.

---

# 30. Failure Handling During the Live Demo

The demo should never stop because one third-party service misbehaves.

---

## Gmail trigger delayed

Use:

```text
POST /api/events
```

with the same Gmail-like event.

Say nothing unless needed; the system still uses the real event pipeline.

---

## Slack API fails

Show:

```text
Action FAILED
```

Then use controlled event mode if necessary.

Do not silently pretend the Slack call worked.

---

## Realtime UI fails

Use:

```text
automatic polling fallback
```

The audience should not need to refresh manually.

---

## LLM output fails

Have fixture fallback ready for primary scenario.

Only use this if necessary.

---

# 31. Controlled Demo Mode

Controlled Demo Mode is acceptable if it:

```text
injects events
```

but still runs through:

```text
Event Router
Verifier
Replanner
Database
Actions
UI
```

It must not:

```text
hard-code frontend animations
```

or:

```text
directly mutate UI state
```

The system architecture should remain real.

---

# 32. Demo Reset

Before every rehearsal:

```text
1. Reset demo database state.
2. Remove old Calendar test events.
3. Clear demo Slack thread if needed.
4. Reset Drive demo folder.
5. Verify connected accounts.
6. Open dashboard.
7. Confirm backend health.
```

Use:

```bash
python scripts/reset_demo.py
```

or equivalent.

---

# 33. Rehearsal Checklist

Run the demo at least three times.

### Rehearsal 1

Focus:

```text
functional correctness
```

---

### Rehearsal 2

Focus:

```text
timing
handoffs
screen switching
```

---

### Rehearsal 3

Focus:

```text
failure recovery
fallback events
presentation polish
```

---

# 34. Judge Question Preparation

Likely questions:

## "How is this different from Zapier?"

Answer:

> “Zapier executes predefined triggers and actions. LoopGraph maintains a persistent semantic model of the desired outcome, checks whether observed evidence satisfies it, and can rewrite dependencies when circumstances change.”

---

## "How do you know when something is done?"

Answer:

> “Every node has an evidence contract. We use deterministic APIs for state and semantic verification for meaning. The node only becomes verified when its evidence contract is satisfied.”

---

## "What prevents duplicate messages?"

Answer:

> “External events are deduplicated using source IDs and actions carry idempotency keys. A verified action with the same idempotency key is never executed twice.”

---

## "Why LangGraph?"

Answer:

> “The workflows are stateful and long-lived. We need resumable execution, waiting, approval interrupts, and recovery rather than stateless prompt chains.”

---

## "Why not just use reminders?"

Answer:

> “Reminders transfer responsibility back to the user. LoopGraph observes whether the outcome occurred and continues or repairs the workflow automatically.”

---

## "Is this hard-coded for refunds?"

Answer:

> “No. Refund, promise, and renewal workflows all compile into the same generic data model: outcome nodes, dependencies, evidence requirements, actions, and recovery operations.”

---

## "What does AI actually do?"

Answer:

> “AI handles semantic work: compiling natural-language obligations into graphs, determining whether new events prove or contradict outcomes, and proposing graph repairs. Deterministic application state is still verified through APIs.”

---

# 35. Metrics to Mention If Asked

For a hackathon, do not invent performance claims.

You may report measured demo/test metrics such as:

```text
Number of supported workflow classes

Number of external apps

P0 tests passed

Number of graph repair types

Action verification rate in test run

Duplicate-event test results
```

Example:

```text
"All 19 P0 reliability tests passed in our final run."
```

Only say this if actually measured.

---

# 36. Demo Success Criteria

The demo is successful if judges clearly see:

- [ ] one natural-language obligation become a graph
- [ ] at least three external apps participate
- [ ] at least one external action occurs
- [ ] evidence changes graph state
- [ ] new information causes replanning
- [ ] stale future action is cancelled
- [ ] human approval gates communication
- [ ] final outcome is verified
- [ ] secondary scenarios prove generality
- [ ] one architecture supports all three workflows

---

# 37. Final Presentation Principle

Do not present LoopGraph as:

```text
"We connected four APIs with an LLM."
```

Present it as:

```text
"We built an outcome engine."
```

The integrations are evidence that the engine can operate in the real world.

The graph, verification, and repair behavior are the product.

The final line should be:

> **LoopGraph owns the outcome, not just the action.**
