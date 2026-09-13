# LoopGraph — Product & MVP Scope

## 1. Purpose

LoopGraph is a multi-app AI agent that converts unfinished real-world objectives into **verifiable outcome graphs**, takes actions across connected applications, monitors new evidence, and repairs its plan when circumstances change.

The system is designed around one principle:

> **LoopGraph owns the outcome, not just the action.**

Sending an email does not mean a goal is complete. Creating a reminder does not mean the obligation has been fulfilled. A LoopGraph workflow is complete only when the required evidence for the intended outcome has been observed and verified.

---

## 2. Hackathon Goal

Build a reliable multi-step AI agent that can:

1. Understand an unfinished real-world objective.
2. Convert it into a graph of dependent outcomes.
3. Take actions across at least three external applications.
4. Observe future events from those applications.
5. Match those events to existing outcome nodes.
6. Verify whether required evidence has been satisfied.
7. Detect missed deadlines, contradictions, or changed conditions.
8. Replan or repair the graph when necessary.
9. Mark a goal complete only when its completion criteria are verified.

The hackathon implementation should demonstrate that the same underlying LoopGraph runtime can handle multiple everyday workflows without hard-coding a separate automation for each use case.

---

## 3. Core Product Definition

### One-sentence definition

**LoopGraph is a self-healing outcome engine that turns everyday obligations into dependency graphs and keeps acting across your apps until the intended result is verified.**

### Core lifecycle

```text
GOAL
  ↓
COMPILE
  ↓
OUTCOME GRAPH
  ↓
ACT
  ↓
OBSERVE
  ↓
VERIFY
  ↓
COMPLETE
  │
  └── if something changes or fails:
          ↓
        REPAIR
          ↓
        ACT AGAIN
```

---

## 4. MVP Use Cases

The MVP supports three primary use-case classes.

### 4.1 Return / Refund

Example objective:

> Return an item and make sure the refund is actually received.

Typical graph:

```text
Receive refund
    ↑
Refund processed
    ↑
Merchant receives return
    ↑
Return item
```

Possible application interactions:

- Gmail: detect return instructions, merchant updates, refund confirmation
- Google Calendar: create return/refund deadlines
- Google Drive: save return labels or receipts
- Optional task/action layer: represent physical actions the user must complete

Key behavior demonstrated:

- sequential dependencies
- deadlines
- waiting states
- evidence-based completion
- automatic cancellation of unnecessary future actions
- deadline changes and replanning

---

### 4.2 Someone Promised Something

Example objective:

> A colleague, professional contact, or friend promised to send a document by Friday.

Typical graph:

```text
Obtain document
    ↑
Receive valid file
    ↑
Wait for promised delivery
```

Possible application interactions:

- Slack: detect the original promise and follow-up messages
- Gmail: alternative communication or attachment delivery
- Google Drive: save and validate the received file
- Google Calendar: track deadline/checkpoint

Key behavior demonstrated:

- human dependency
- overdue detection
- follow-up generation
- reassignment when responsibility changes
- graph restructuring
- attachment/evidence verification

Example recovery:

```text
Sarah promised document
        ↓
Friday passes
        ↓
Follow up
        ↓
Sarah says Mike has latest version
        ↓
Replace dependency:
Sarah → Mike
        ↓
Continue workflow
```

---

### 4.3 Paperwork / Renewal

Example objective:

> Renew renter's insurance and make sure the landlord receives valid proof.

Typical graph:

```text
Landlord has valid proof
        ↑
Landlord acknowledges receipt
        ↑
Send renewed policy
        ↑
Receive renewed policy
        ↑
Renew policy
```

Possible application interactions:

- Gmail: renewal notice, updated policy, landlord communication
- Google Drive: locate old policy, save new policy, retrieve proof
- Google Calendar: expiration and submission deadlines
- Optional Slack or other communication app if relevant

Key behavior demonstrated:

- chained administrative tasks
- document retrieval
- document evidence
- deadline management
- third-party acknowledgement
- verification of final outcome

---

## 5. Required MVP Capabilities

### 5.1 Goal Intake

LoopGraph must accept a goal from at least one of these sources:

- user-entered natural language
- Gmail message
- Slack message

Example:

> "John said he will send the presentation by Friday."

The system should infer:

- intended outcome
- actors involved
- deadline if present
- required evidence
- initial actions
- dependencies

---

### 5.2 Outcome Graph Compilation

The system must convert the goal into structured nodes and edges.

Each node should represent an **outcome**, not merely an instruction.

Bad:

```text
Send email
```

Better:

```text
Obtain signed lease
```

Actions may support a node, but the node should describe the desired state.

Each outcome node should support:

- title
- description
- status
- deadline
- owner/actor if relevant
- dependencies
- evidence requirements
- available/recommended actions
- recovery strategy

---

### 5.3 Multi-App Actions

LoopGraph must take meaningful actions across at least three external applications.

Target integrations:

- Gmail
- Slack
- Google Drive
- Google Calendar

Not every use case must use every integration.

The overall product must clearly demonstrate multi-app execution.

---

### 5.4 Event Observation

LoopGraph must be able to process future events such as:

- new Gmail message
- new Slack message
- document received or created
- calendar deadline reached
- user approval
- user rejection
- action execution result

External events should be normalized into a common internal event format before reaching the reasoning layer.

---

### 5.5 Evidence-Based Verification

Every important outcome node must define what counts as proof of completion.

Example:

```text
Node:
Receive $129 refund

Valid evidence:
Merchant confirms that the $129 refund was processed.

Not sufficient:
Follow-up email was sent.
Return was initiated.
Calendar reminder exists.
```

The system must distinguish:

```text
ACTION EXECUTED
```

from:

```text
OUTCOME VERIFIED
```

---

### 5.6 Deadline Handling

The system must detect when a deadline or expected event has passed without sufficient evidence.

Example:

```text
Expected:
Presentation by Friday

Observed:
No valid presentation received

Result:
Deadline missed
```

That event should be routed back into the reasoning/recovery layer.

---

### 5.7 Replanning / Self-Healing

LoopGraph must demonstrate at least one workflow where new information invalidates the current plan.

Examples:

- refund deadline changes
- promised document owner changes
- renewal instructions change
- expected source becomes unavailable

The system should modify the existing graph rather than discard it and start from scratch.

---

### 5.8 Human Approval

Actions should follow a simple risk policy.

#### Autonomous actions

Examples:

- search email
- search Drive
- create/update internal graph state
- create a low-risk calendar reminder
- retrieve information
- save evidence

#### Approval-required actions

Examples:

- send email
- send Slack message
- delete/cancel calendar event
- external communication representing the user

#### Not supported autonomously in MVP

- payments
- purchases
- legal submissions
- financial transfers
- irreversible high-risk actions

---

## 6. Definition of Done

### Outcome node is complete when:

1. Its dependencies are satisfied.
2. Its evidence contract is satisfied.
3. Required actions have been verified where applicable.
4. No unresolved contradiction invalidates the evidence.

### Entire loop is complete when:

1. The root goal's evidence requirements are satisfied.
2. All required dependent nodes are verified or explicitly superseded.
3. Pending recovery actions are cancelled.
4. Future reminders/follow-ups that are no longer needed are removed or marked cancelled.
5. The system records why the loop was considered complete.

---

## 7. Reliability Principles

### 7.1 Verify state after acting

Do not treat an API success response as proof that the intended state exists.

Example:

```text
Create Calendar event
        ↓
Read event back
        ↓
Compare expected state with actual state
        ↓
Mark action VERIFIED
```

### 7.2 Use APIs for state, AI for meaning

Use deterministic APIs wherever possible.

Examples:

- Calendar API confirms whether an event exists.
- Drive API confirms whether a file exists.
- Gmail API confirms whether a message was sent.

Use AI for semantic questions.

Examples:

- Does this email prove the refund happened?
- Is this the correct renewed insurance policy?
- Does this Slack message supersede the previous promise?

### 7.3 Idempotency

The system should avoid duplicate actions when processing the same external event more than once.

Examples:

- do not create the same calendar reminder twice
- do not send the same follow-up twice
- do not save the same evidence repeatedly

### 7.4 Explainability

Every important graph change should have a human-readable reason.

Example:

```text
Refund deadline updated from Sep 21 to Sep 28
because merchant email on Sep 18 stated:
"Processing may take five additional business days."
```

---

## 8. Explicitly Out of Scope

The following are **not MVP requirements**:

- browser automation
- arbitrary website navigation
- autonomous payments
- autonomous purchasing
- legal form submission
- banking integrations
- production-scale multi-tenancy
- mobile application
- voice interface
- vector database
- generic RAG infrastructure
- Neo4j or dedicated graph database
- Kafka
- Kubernetes
- Celery
- complex distributed scheduling
- multi-LLM routing
- autonomous web research
- full task-management product
- support for arbitrary third-party apps
- production-grade permission administration
- production-grade billing

If a feature does not materially improve the three selected workflows or demonstrate LoopGraph's core architecture, it should be deferred.

---

## 9. Success Criteria for the Hackathon

The MVP is considered successful if the team can demonstrate:

### Functional

- [ ] A natural-language or app-originated goal is converted into an outcome graph.
- [ ] The graph contains meaningful dependencies.
- [ ] At least three external apps are involved across the demonstrated workflows.
- [ ] The system takes at least one real external action.
- [ ] Incoming events update an existing graph.
- [ ] Evidence can automatically verify an outcome.
- [ ] A missed deadline can trigger recovery.
- [ ] New information can cause the graph to be repaired/replanned.
- [ ] A future action can be cancelled because the desired outcome was achieved early.

### Reliability

- [ ] External actions are read back and verified.
- [ ] Duplicate event processing does not create duplicate actions.
- [ ] Medium-risk communication requires approval.
- [ ] The system records evidence and reasoning for important state transitions.
- [ ] An action is never treated as equivalent to the final outcome.

### Demo

- [ ] Return / Refund workflow can be demonstrated end-to-end.
- [ ] Promise workflow demonstrates human dependency and reassignment/recovery.
- [ ] Paperwork / Renewal workflow demonstrates document handling and final acknowledgement.
- [ ] UI visibly shows graph-state changes.
- [ ] Activity feed explains actions and graph repairs.
- [ ] The same runtime/schema is used for all three workflows.

---

## 10. MVP Product Principles

1. **Outcomes over tasks**  
   Model desired real-world states, not just actions.

2. **Evidence over assumptions**  
   Completion must be grounded in observable evidence.

3. **Repair over failure**  
   When reality changes, modify the graph and continue.

4. **One engine, many workflows**  
   Do not hard-code separate automations for each use case.

5. **Safe autonomy**  
   Automate low-risk actions; request approval for external communication.

6. **Reliability over feature count**  
   Three working workflows are more valuable than ten partially implemented ones.

7. **Visible reasoning**  
   The UI should make it clear why the graph changed and why a goal is considered complete.

---

## 11. Non-Goals for the Hackathon Demo

LoopGraph does not need to prove that it can automate all of a user's life.

The hackathon prototype only needs to prove the architectural thesis:

> **Everyday unfinished obligations can be represented as verifiable outcome graphs that AI agents can execute, observe, repair, and complete across multiple applications.**

If the three selected workflows convincingly demonstrate that thesis, the MVP has achieved its purpose.
