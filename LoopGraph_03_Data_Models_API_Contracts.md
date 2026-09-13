# LoopGraph — Data Models & API Contracts

## 1. Purpose

This document defines the shared data contracts for LoopGraph.

All backend modules, agents, integrations, and frontend components should use these structures consistently.

The goal is to let teammates work independently while preserving compatibility across:

- Outcome Compiler
- Event Router
- Evidence Verifier
- Replanner
- Action Executor
- Scheduler
- Persistence Layer
- Frontend / React Flow UI

The core rule is:

> **No module should depend on another module's internal implementation. Modules communicate through validated structured contracts.**

---

# 2. Canonical Domain Objects

LoopGraph uses the following core objects:

```text
Loop
OutcomeNode
Edge
Event
Evidence
Action
Approval
ActivityLog
```

These objects form the shared domain model.

---

# 3. Global Conventions

## 3.1 IDs

Use string IDs everywhere.

Recommended format:

```text
loop_<uuid>
node_<uuid>
edge_<uuid>
event_<uuid>
evidence_<uuid>
action_<uuid>
approval_<uuid>
activity_<uuid>
```

Example:

```text
loop_7f6be8e6
node_2a19d7bf
```

Database-generated UUIDs are acceptable as long as the API exposes strings.

---

## 3.2 Timestamps

Use ISO 8601 timestamps with timezone information.

Example:

```text
2026-09-13T11:42:00-04:00
```

All backend timestamps should be timezone-aware.

---

## 3.3 Amounts

Represent monetary values using:

```json
{
  "amount": 129.00,
  "currency": "USD"
}
```

Do not encode:

```text
"$129"
```

as the canonical internal value.

---

## 3.4 Confidence

Semantic outputs may include confidence as:

```text
0.0 to 1.0
```

Confidence must never be the sole reason for changing a critical state.

---

# 4. Enumerations

## 4.1 LoopStatus

```text
ACTIVE
WAITING
BLOCKED
COMPLETED
FAILED
CANCELLED
```

Definitions:

### ACTIVE

At least one node is actionable.

### WAITING

The system is waiting for external evidence or a future event.

### BLOCKED

Progress cannot continue because a dependency or required user action is unresolved.

### COMPLETED

The root objective is verified complete.

### FAILED

The system has reached an unrecoverable state.

### CANCELLED

The loop was intentionally terminated.

---

## 4.2 NodeStatus

```text
PENDING
ACTIVE
WAITING
BLOCKED
VERIFIED
FAILED
CANCELLED
SUPERSEDED
```

### PENDING

Node exists but its dependencies are not yet satisfied.

### ACTIVE

Node is eligible for action.

### WAITING

Relevant action has occurred and the node is waiting for evidence.

### BLOCKED

Node cannot proceed because a dependency is unresolved.

### VERIFIED

Evidence contract has been satisfied.

### FAILED

Node execution or evidence verification failed and recovery did not succeed.

### CANCELLED

Node is intentionally no longer required.

### SUPERSEDED

Node was replaced by a new node due to changed information.

---

## 4.3 EdgeType

```text
DEPENDS_ON
PROVES
BLOCKS
TRIGGERS
SUPERSEDES
CONTRADICTS
```

Typical usage:

```text
RefundProcessed DEPENDS_ON MerchantReceivedReturn

RefundEmail PROVES RefundProcessed

MissingDocument BLOCKS SubmitPaperwork

DeadlineReached TRIGGERS FollowUp

MikeProvidesDocument SUPERSEDES SarahProvidesDocument
```

---

## 4.4 EventType

```text
MESSAGE_RECEIVED
MESSAGE_SENT
DOCUMENT_CREATED
DOCUMENT_UPDATED
DOCUMENT_FOUND
CALENDAR_EVENT_CREATED
CALENDAR_EVENT_UPDATED
CALENDAR_EVENT_DELETED
DEADLINE_REACHED
ACTION_COMPLETED
ACTION_FAILED
USER_APPROVED
USER_REJECTED
USER_INPUT
SYSTEM_EVENT
```

---

## 4.5 EvidenceRelationship

```text
PROVES
CONTRADICTS
SUPERSEDES
PARTIALLY_SUPPORTS
INSUFFICIENT
UNRELATED
```

---

## 4.6 ActionStatus

```text
PROPOSED
AWAITING_APPROVAL
APPROVED
EXECUTING
EXECUTED
VERIFIED
FAILED
CANCELLED
```

---

## 4.7 ApprovalStatus

```text
PENDING
APPROVED
REJECTED
EXPIRED
```

---

## 4.8 RiskLevel

```text
LOW
MEDIUM
HIGH
```

Recommended policy:

```text
LOW
→ may execute automatically

MEDIUM
→ requires human approval

HIGH
→ not autonomously supported in hackathon MVP
```

---

# 5. Loop Model

A `Loop` represents one real-world objective.

Example:

```text
Receive the $129 refund for Order #A1298.
```

## 5.1 Pydantic Model

```python
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

class Loop(BaseModel):
    id: str
    user_id: str

    title: str
    goal: str
    status: LoopStatus

    root_node_id: str

    source_event_ids: list[str] = []
    node_ids: list[str] = []

    created_at: datetime
    updated_at: datetime

    completed_at: Optional[datetime] = None
```

---

## 5.2 Example JSON

```json
{
  "id": "loop_refund_001",
  "user_id": "user_001",
  "title": "Sony headphones refund",
  "goal": "Receive the $129 refund for Order #A1298.",
  "status": "WAITING",
  "root_node_id": "node_refund_received",
  "source_event_ids": ["event_return_approved"],
  "node_ids": [
    "node_return_item",
    "node_merchant_received",
    "node_refund_received"
  ],
  "created_at": "2026-09-13T09:35:00-04:00",
  "updated_at": "2026-09-13T10:10:00-04:00",
  "completed_at": null
}
```

---

# 6. OutcomeNode Model

An `OutcomeNode` describes a desired state.

It should not primarily describe an API action.

Bad:

```text
Send email
```

Better:

```text
Merchant confirms refund processing
```

---

## 6.1 Pydantic Model

```python
from typing import Optional

class OutcomeNode(BaseModel):
    id: str
    loop_id: str

    title: str
    description: Optional[str] = None

    status: NodeStatus

    owner: Optional[str] = None
    deadline: Optional[datetime] = None

    depends_on: list[str] = []

    evidence_requirement_ids: list[str] = []
    evidence_ids: list[str] = []

    action_ids: list[str] = []

    recovery_strategy: Optional[str] = None

    metadata: dict = {}

    created_at: datetime
    updated_at: datetime
```

---

## 6.2 Example JSON

```json
{
  "id": "node_refund_received",
  "loop_id": "loop_refund_001",
  "title": "Receive $129 refund",
  "description": "Merchant confirms the refund for Order #A1298 has been processed.",
  "status": "WAITING",
  "owner": "merchant",
  "deadline": "2026-09-21T17:00:00-04:00",
  "depends_on": ["node_merchant_received"],
  "evidence_requirement_ids": ["evidence_req_refund_confirmation"],
  "evidence_ids": [],
  "action_ids": ["action_refund_checkpoint"],
  "recovery_strategy": "If overdue, prepare a merchant follow-up and reschedule the checkpoint.",
  "metadata": {
    "order_id": "A1298",
    "amount": 129.0,
    "currency": "USD"
  },
  "created_at": "2026-09-13T09:35:00-04:00",
  "updated_at": "2026-09-13T10:10:00-04:00"
}
```

---

# 7. Edge Model

Edges describe semantic relationships between graph nodes.

Do not infer graph topology only from arrays in the UI.

Persist relationships explicitly.

---

## 7.1 Pydantic Model

```python
class Edge(BaseModel):
    id: str
    loop_id: str

    source_node_id: str
    target_node_id: str

    relationship: EdgeType

    reason: Optional[str] = None

    created_at: datetime
```

---

## 7.2 Example

```json
{
  "id": "edge_merchant_refund",
  "loop_id": "loop_refund_001",
  "source_node_id": "node_refund_received",
  "target_node_id": "node_merchant_received",
  "relationship": "DEPENDS_ON",
  "reason": "Refund can only be expected after the merchant confirms receipt of the return.",
  "created_at": "2026-09-13T09:35:00-04:00"
}
```

---

# 8. EvidenceRequirement Model

An `EvidenceRequirement` defines what must be true before a node can become `VERIFIED`.

---

## 8.1 EvidenceType

Recommended values:

```text
EMAIL_CONFIRMATION
SLACK_CONFIRMATION
DOCUMENT_RECEIVED
DOCUMENT_VALID
CALENDAR_STATE
HUMAN_CONFIRMATION
API_STATE
CUSTOM
```

---

## 8.2 Pydantic Model

```python
class EvidenceRequirement(BaseModel):
    id: str
    node_id: str

    type: str

    description: str

    source_apps: list[str]

    required_fields: dict = {}

    must_all_match: bool = True

    created_at: datetime
```

---

## 8.3 Example

```json
{
  "id": "evidence_req_refund_confirmation",
  "node_id": "node_refund_received",
  "type": "EMAIL_CONFIRMATION",
  "description": "Merchant confirms the $129 refund for Order #A1298 was processed.",
  "source_apps": ["gmail"],
  "required_fields": {
    "order_id": "A1298",
    "amount": 129.0,
    "currency": "USD"
  },
  "must_all_match": true,
  "created_at": "2026-09-13T09:35:00-04:00"
}
```

---

# 9. Evidence Model

`Evidence` is an observed artifact that may satisfy or modify a node.

Evidence is separate from the event that produced it.

---

## 9.1 Pydantic Model

```python
class Evidence(BaseModel):
    id: str

    node_id: str
    event_id: str

    relationship: EvidenceRelationship

    confidence: float = Field(ge=0.0, le=1.0)

    reason: str

    extracted_fields: dict = {}

    verified: bool = False

    created_at: datetime
```

---

## 9.2 Example — Positive Evidence

```json
{
  "id": "evidence_refund_email",
  "node_id": "node_refund_received",
  "event_id": "event_refund_confirmed",
  "relationship": "PROVES",
  "confidence": 0.98,
  "reason": "Merchant explicitly confirms the $129 refund for Order #A1298 was processed.",
  "extracted_fields": {
    "order_id": "A1298",
    "amount": 129.0,
    "currency": "USD"
  },
  "verified": true,
  "created_at": "2026-09-18T14:05:00-04:00"
}
```

---

## 9.3 Example — Insufficient Evidence

```json
{
  "id": "evidence_draft_presentation",
  "node_id": "node_final_presentation",
  "event_id": "event_file_arrived",
  "relationship": "INSUFFICIENT",
  "confidence": 0.93,
  "reason": "The received file is labeled as a draft and does not satisfy the request for the final client presentation.",
  "extracted_fields": {
    "filename": "client_presentation_draft_v2.pptx"
  },
  "verified": true,
  "created_at": "2026-09-18T15:15:00-04:00"
}
```

---

# 10. Event Model

Every external input must be normalized into the same `Event` structure before it reaches the reasoning layer.

The reasoning system should not depend on Gmail-specific or Slack-specific payload structures.

---

## 10.1 Event Model

```python
class Event(BaseModel):
    id: str

    source_app: str
    event_type: EventType

    external_id: Optional[str] = None

    timestamp: datetime

    actor: Optional[str] = None

    subject: Optional[str] = None
    content: Optional[str] = None

    attachments: list[dict] = []

    metadata: dict = {}

    linked_loop_id: Optional[str] = None

    processed: bool = False
```

---

## 10.2 Gmail Example

```json
{
  "id": "event_refund_confirmed",
  "source_app": "gmail",
  "event_type": "MESSAGE_RECEIVED",
  "external_id": "gmail_msg_8891",
  "timestamp": "2026-09-18T14:03:00-04:00",
  "actor": "returns@merchant.com",
  "subject": "Your refund has been processed",
  "content": "Your $129.00 refund for Order #A1298 has been processed.",
  "attachments": [],
  "metadata": {
    "thread_id": "thread_444"
  },
  "linked_loop_id": null,
  "processed": false
}
```

---

## 10.3 Slack Example

```json
{
  "id": "event_sarah_reassigns",
  "source_app": "slack",
  "event_type": "MESSAGE_RECEIVED",
  "external_id": "slack_msg_101",
  "timestamp": "2026-09-18T12:20:00-04:00",
  "actor": "Sarah",
  "subject": null,
  "content": "Mike actually has the final version. Please get it from him.",
  "attachments": [],
  "metadata": {
    "channel_id": "C123",
    "thread_ts": "1690000123.111"
  },
  "linked_loop_id": "loop_presentation_001",
  "processed": false
}
```

---

## 10.4 Deadline Example

```json
{
  "id": "event_deadline_presentation",
  "source_app": "loopgraph",
  "event_type": "DEADLINE_REACHED",
  "external_id": "deadline_node_sarah_presentation",
  "timestamp": "2026-09-18T17:00:00-04:00",
  "actor": "system",
  "subject": "Deadline reached",
  "content": "Deadline reached for Sarah to provide the final presentation.",
  "attachments": [],
  "metadata": {
    "node_id": "node_sarah_presentation"
  },
  "linked_loop_id": "loop_presentation_001",
  "processed": false
}
```

---

# 11. Action Model

An `Action` represents a concrete external or internal operation.

Examples:

```text
CREATE_CALENDAR_EVENT
SEND_EMAIL
SEND_SLACK_MESSAGE
SAVE_DRIVE_FILE
UPDATE_CALENDAR_EVENT
CANCEL_CALENDAR_EVENT
SEARCH_GMAIL
SEARCH_DRIVE
```

---

## 11.1 Pydantic Model

```python
class Action(BaseModel):
    id: str

    loop_id: str
    node_id: Optional[str] = None

    app: str
    action_type: str

    parameters: dict

    risk_level: RiskLevel

    requires_approval: bool

    status: ActionStatus

    idempotency_key: str

    external_id: Optional[str] = None

    verification_method: Optional[str] = None

    error: Optional[str] = None

    created_at: datetime
    updated_at: datetime
```

---

## 11.2 Example — Calendar Action

```json
{
  "id": "action_return_deadline",
  "loop_id": "loop_refund_001",
  "node_id": "node_return_item",
  "app": "google_calendar",
  "action_type": "CREATE_CALENDAR_EVENT",
  "parameters": {
    "title": "Return Sony headphones",
    "start": "2026-09-16T09:00:00-04:00",
    "end": "2026-09-16T09:15:00-04:00"
  },
  "risk_level": "LOW",
  "requires_approval": false,
  "status": "PROPOSED",
  "idempotency_key": "loop_refund_001:return_deadline",
  "external_id": null,
  "verification_method": "READ_AFTER_WRITE",
  "error": null,
  "created_at": "2026-09-13T09:38:00-04:00",
  "updated_at": "2026-09-13T09:38:00-04:00"
}
```

---

## 11.3 Example — Slack Follow-Up

```json
{
  "id": "action_followup_sarah",
  "loop_id": "loop_presentation_001",
  "node_id": "node_sarah_presentation",
  "app": "slack",
  "action_type": "SEND_SLACK_MESSAGE",
  "parameters": {
    "channel_id": "C123",
    "thread_ts": "1690000123.111",
    "message": "Hi Sarah, just checking in on the final client presentation you mentioned you'd send by Friday."
  },
  "risk_level": "MEDIUM",
  "requires_approval": true,
  "status": "AWAITING_APPROVAL",
  "idempotency_key": "loop_presentation_001:sarah_followup:1",
  "external_id": null,
  "verification_method": "READ_THREAD_AFTER_SEND",
  "error": null,
  "created_at": "2026-09-18T17:02:00-04:00",
  "updated_at": "2026-09-18T17:02:00-04:00"
}
```

---

# 12. Approval Model

Approvals connect human decisions to actions.

---

## 12.1 Pydantic Model

```python
class Approval(BaseModel):
    id: str

    action_id: str
    loop_id: str

    status: ApprovalStatus

    requested_at: datetime

    resolved_at: Optional[datetime] = None

    user_comment: Optional[str] = None
```

---

## 12.2 Example

```json
{
  "id": "approval_followup_sarah",
  "action_id": "action_followup_sarah",
  "loop_id": "loop_presentation_001",
  "status": "PENDING",
  "requested_at": "2026-09-18T17:02:00-04:00",
  "resolved_at": null,
  "user_comment": null
}
```

---

# 13. ActivityLog Model

Every meaningful state transition should produce a user-readable activity entry.

---

## 13.1 Pydantic Model

```python
class ActivityLog(BaseModel):
    id: str

    loop_id: str

    activity_type: str

    message: str

    metadata: dict = {}

    created_at: datetime
```

---

## 13.2 Examples

```json
{
  "id": "activity_001",
  "loop_id": "loop_refund_001",
  "activity_type": "NODE_VERIFIED",
  "message": "Return item verified from UPS drop-off confirmation.",
  "metadata": {
    "node_id": "node_return_item",
    "evidence_id": "evidence_ups_dropoff"
  },
  "created_at": "2026-09-14T13:04:00-04:00"
}
```

```json
{
  "id": "activity_002",
  "loop_id": "loop_refund_001",
  "activity_type": "GRAPH_REPAIRED",
  "message": "Refund deadline moved from Sep 21 to Sep 28 because the merchant announced a 5-business-day processing delay.",
  "metadata": {
    "node_id": "node_refund_received",
    "old_deadline": "2026-09-21T17:00:00-04:00",
    "new_deadline": "2026-09-28T17:00:00-04:00"
  },
  "created_at": "2026-09-18T09:41:00-04:00"
}
```

---

# 14. Agent Contracts

The following contracts define how agent modules communicate.

---

# 15. Outcome Compiler Contract

The Outcome Compiler receives raw goal context and returns a proposed graph.

It must not directly perform external actions.

---

## 15.1 Input

```python
class CompileGoalRequest(BaseModel):
    user_id: str

    user_goal: Optional[str] = None

    source_event: Optional[Event] = None

    available_apps: list[str]
```

At least one of:

```text
user_goal
source_event
```

must be present.

---

## 15.2 Output

```python
class CompiledGraph(BaseModel):
    loop: Loop

    nodes: list[OutcomeNode]

    edges: list[Edge]

    evidence_requirements: list[EvidenceRequirement]

    proposed_actions: list[Action]

    assumptions: list[str] = []

    clarification_needed: bool = False

    clarification_question: Optional[str] = None
```

---

## 15.3 Example

```json
{
  "loop": {
    "id": "loop_refund_001",
    "user_id": "user_001",
    "title": "Sony headphones refund",
    "goal": "Receive the $129 refund for Order #A1298.",
    "status": "ACTIVE",
    "root_node_id": "node_refund_received",
    "source_event_ids": ["event_return_approved"],
    "node_ids": [],
    "created_at": "2026-09-13T09:35:00-04:00",
    "updated_at": "2026-09-13T09:35:00-04:00",
    "completed_at": null
  },
  "nodes": [],
  "edges": [],
  "evidence_requirements": [],
  "proposed_actions": [],
  "assumptions": [
    "Merchant refund confirmation is considered sufficient MVP evidence of refund completion."
  ],
  "clarification_needed": false,
  "clarification_question": null
}
```

---

# 16. Event Router Contract

The Event Router answers:

> Which existing loop(s), if any, might this event affect?

---

## 16.1 Input

```python
class RouteEventRequest(BaseModel):
    event: Event
```

---

## 16.2 Output

```python
class LoopMatch(BaseModel):
    loop_id: str
    confidence: float
    reason: str

class RouteEventResponse(BaseModel):
    matches: list[LoopMatch]
    create_new_loop_candidate: bool = False
```

---

## 16.3 Example

```json
{
  "matches": [
    {
      "loop_id": "loop_refund_001",
      "confidence": 0.99,
      "reason": "The event references Order #A1298 and a $129 refund, both present in the active loop."
    }
  ],
  "create_new_loop_candidate": false
}
```

---

# 17. Evidence Verifier Contract

The Evidence Verifier determines how an event affects one or more nodes.

---

## 17.1 Input

```python
class VerifyEventRequest(BaseModel):
    loop: Loop
    nodes: list[OutcomeNode]
    requirements: list[EvidenceRequirement]

    event: Event
```

---

## 17.2 Output

```python
class NodeEvidenceDecision(BaseModel):
    node_id: str

    relationship: EvidenceRelationship

    confidence: float

    reason: str

    extracted_fields: dict = {}

    evidence_satisfies_requirement: bool = False

class VerifyEventResponse(BaseModel):
    decisions: list[NodeEvidenceDecision]

    requires_replan: bool = False
```

---

## 17.3 Positive Example

```json
{
  "decisions": [
    {
      "node_id": "node_refund_received",
      "relationship": "PROVES",
      "confidence": 0.98,
      "reason": "Merchant explicitly confirms the $129 refund for Order #A1298 was processed.",
      "extracted_fields": {
        "amount": 129.0,
        "order_id": "A1298"
      },
      "evidence_satisfies_requirement": true
    }
  ],
  "requires_replan": false
}
```

---

## 17.4 Replanning Example

```json
{
  "decisions": [
    {
      "node_id": "node_sarah_presentation",
      "relationship": "SUPERSEDES",
      "confidence": 0.95,
      "reason": "Sarah states that Mike now owns the latest presentation.",
      "extracted_fields": {
        "new_owner": "Mike"
      },
      "evidence_satisfies_requirement": false
    }
  ],
  "requires_replan": true
}
```

---

# 18. Replanner Contract

The Replanner must operate on the existing graph.

It should return **graph operations**, not a completely regenerated graph.

This preserves history and reduces accidental state loss.

---

## 18.1 GraphOperationType

```text
ADD_NODE
UPDATE_NODE
SUPERSEDE_NODE
CANCEL_NODE
VERIFY_NODE

ADD_EDGE
REMOVE_EDGE

UPDATE_DEADLINE
ADD_EVIDENCE_REQUIREMENT
UPDATE_EVIDENCE_REQUIREMENT

ADD_ACTION
CANCEL_ACTION
```

---

## 18.2 Input

```python
class ReplanRequest(BaseModel):
    loop: Loop
    nodes: list[OutcomeNode]
    edges: list[Edge]

    triggering_event: Event

    evidence_decisions: list[NodeEvidenceDecision]
```

---

## 18.3 Operation Model

```python
class GraphOperation(BaseModel):
    type: str

    target_id: Optional[str] = None

    payload: dict = {}

    reason: str
```

---

## 18.4 Output

```python
class ReplanResponse(BaseModel):
    operations: list[GraphOperation]

    proposed_actions: list[Action]

    summary: str
```

---

## 18.5 Example — Promise Reassignment

```json
{
  "operations": [
    {
      "type": "SUPERSEDE_NODE",
      "target_id": "node_sarah_presentation",
      "payload": {},
      "reason": "Sarah stated that Mike owns the latest presentation."
    },
    {
      "type": "ADD_NODE",
      "target_id": null,
      "payload": {
        "id": "node_mike_presentation",
        "loop_id": "loop_presentation_001",
        "title": "Mike provides final presentation",
        "status": "ACTIVE",
        "owner": "Mike"
      },
      "reason": "Responsibility moved from Sarah to Mike."
    },
    {
      "type": "ADD_EDGE",
      "target_id": null,
      "payload": {
        "source_node_id": "node_final_presentation",
        "target_node_id": "node_mike_presentation",
        "relationship": "DEPENDS_ON"
      },
      "reason": "Final presentation now depends on Mike providing the file."
    }
  ],
  "proposed_actions": [
    {
      "id": "action_request_mike",
      "loop_id": "loop_presentation_001",
      "node_id": "node_mike_presentation",
      "app": "slack",
      "action_type": "SEND_SLACK_MESSAGE",
      "parameters": {
        "message": "Hi Mike, Sarah mentioned you have the latest client presentation. Could you send it over?"
      },
      "risk_level": "MEDIUM",
      "requires_approval": true,
      "status": "AWAITING_APPROVAL",
      "idempotency_key": "loop_presentation_001:request_mike:1",
      "external_id": null,
      "verification_method": "READ_THREAD_AFTER_SEND",
      "error": null,
      "created_at": "2026-09-18T12:21:00-04:00",
      "updated_at": "2026-09-18T12:21:00-04:00"
    }
  ],
  "summary": "Sarah's responsibility was superseded and a new dependency on Mike was added."
}
```

---

# 19. Action Executor Contract

The Action Executor is responsible for integration-specific execution.

No reasoning module should call Gmail, Slack, Drive, or Calendar directly.

---

## 19.1 Input

```python
class ExecuteActionRequest(BaseModel):
    action: Action
```

---

## 19.2 Output

```python
class ExecuteActionResponse(BaseModel):
    action_id: str

    success: bool

    external_id: Optional[str] = None

    raw_result: dict = {}

    error: Optional[str] = None
```

---

## 19.3 Example

```json
{
  "action_id": "action_return_deadline",
  "success": true,
  "external_id": "gcal_event_789",
  "raw_result": {
    "calendar_id": "primary"
  },
  "error": null
}
```

---

# 20. Action Verification Contract

After execution, verify the external state.

---

## 20.1 Input

```python
class VerifyActionRequest(BaseModel):
    action: Action
    execution_result: ExecuteActionResponse
```

---

## 20.2 Output

```python
class VerifyActionResponse(BaseModel):
    action_id: str

    verified: bool

    observed_state: dict = {}

    reason: str
```

---

## 20.3 Example

```json
{
  "action_id": "action_return_deadline",
  "verified": true,
  "observed_state": {
    "title": "Return Sony headphones",
    "start": "2026-09-16T09:00:00-04:00"
  },
  "reason": "The Calendar event exists and matches the requested title and date."
}
```

---

# 21. Deadline Scheduler Contract

The scheduler emits events.

It does not directly trigger recovery behavior.

---

## 21.1 Query

Conceptually:

```sql
SELECT *
FROM outcome_nodes
WHERE deadline <= NOW()
AND status IN ('ACTIVE', 'WAITING');
```

---

## 21.2 Output

For each overdue node, generate:

```text
DEADLINE_REACHED
```

event.

Example:

```json
{
  "id": "event_deadline_sarah",
  "source_app": "loopgraph",
  "event_type": "DEADLINE_REACHED",
  "external_id": "deadline:node_sarah_presentation",
  "timestamp": "2026-09-18T17:00:00-04:00",
  "actor": "system",
  "subject": "Deadline reached",
  "content": "Sarah's promised delivery deadline has passed.",
  "attachments": [],
  "metadata": {
    "node_id": "node_sarah_presentation"
  },
  "linked_loop_id": "loop_presentation_001",
  "processed": false
}
```

---

# 22. API Endpoints

The following REST API is sufficient for the hackathon frontend.

---

## 22.1 Create Goal

```text
POST /api/loops
```

Request:

```json
{
  "goal": "Make sure I receive my $129 refund.",
  "source_event_id": null
}
```

Response:

```json
{
  "loop_id": "loop_refund_001",
  "status": "ACTIVE"
}
```

---

## 22.2 List Loops

```text
GET /api/loops
```

Response:

```json
{
  "loops": []
}
```

---

## 22.3 Get Loop Detail

```text
GET /api/loops/{loop_id}
```

Response:

```json
{
  "loop": {},
  "nodes": [],
  "edges": [],
  "evidence": [],
  "actions": [],
  "activity": []
}
```

This endpoint should provide enough data to render the entire dashboard.

---

## 22.4 Get Pending Approvals

```text
GET /api/approvals
```

Response:

```json
{
  "approvals": []
}
```

---

## 22.5 Approve Action

```text
POST /api/approvals/{approval_id}/approve
```

Request:

```json
{
  "edited_parameters": null,
  "comment": null
}
```

Response:

```json
{
  "status": "APPROVED",
  "action_id": "action_followup_sarah"
}
```

---

## 22.6 Reject Action

```text
POST /api/approvals/{approval_id}/reject
```

Request:

```json
{
  "comment": "Don't send this yet."
}
```

---

## 22.7 Receive External Webhook

```text
POST /api/webhooks/composio
```

Responsibilities:

```text
1. validate webhook
2. normalize payload into Event
3. deduplicate event
4. persist event
5. route event
6. process affected loop
7. return quickly
```

Response:

```json
{
  "accepted": true,
  "event_id": "event_..."
}
```

---

## 22.8 Manual Event Injection

Useful for hackathon demos and tests.

```text
POST /api/events
```

Request:

```json
{
  "source_app": "gmail",
  "event_type": "MESSAGE_RECEIVED",
  "actor": "merchant@example.com",
  "content": "Your $129 refund has been processed.",
  "metadata": {
    "order_id": "A1298"
  }
}
```

This endpoint should be disabled or protected outside development.

---

# 23. Frontend Data Contract

The frontend should not reconstruct business logic.

It receives state from the backend and renders it.

---

## 23.1 Loop Detail DTO

Recommended frontend payload:

```json
{
  "loop": {
    "id": "loop_refund_001",
    "title": "Sony headphones refund",
    "goal": "Receive the $129 refund for Order #A1298.",
    "status": "WAITING"
  },
  "graph": {
    "nodes": [],
    "edges": []
  },
  "evidence": [],
  "actions": [],
  "approvals": [],
  "activity": []
}
```

---

## 23.2 React Flow Node Mapping

Backend node:

```json
{
  "id": "node_refund_received",
  "title": "Receive $129 refund",
  "status": "WAITING",
  "deadline": "2026-09-21T17:00:00-04:00"
}
```

Frontend React Flow representation:

```ts
{
  id: "node_refund_received",
  type: "outcome",
  data: {
    title: "Receive $129 refund",
    status: "WAITING",
    deadline: "2026-09-21T17:00:00-04:00"
  },
  position: {
    x: 0,
    y: 0
  }
}
```

Layout positions should remain a frontend concern.

---

# 24. Persistence Expectations

The following data should persist across process restarts:

```text
Loops
Nodes
Edges
Events
Evidence
Actions
Approvals
Activity Logs
```

LangGraph checkpoint state may also persist, but Supabase/Postgres remains the product source of truth.

---

# 25. Database Table Mapping

Recommended Supabase/Postgres tables:

```text
loops
outcome_nodes
edges
events
evidence_requirements
evidence
actions
approvals
activity_logs
```

---

## 25.1 loops

Key columns:

```text
id
user_id
title
goal
status
root_node_id
created_at
updated_at
completed_at
```

---

## 25.2 outcome_nodes

```text
id
loop_id
title
description
status
owner
deadline
recovery_strategy
metadata JSONB
created_at
updated_at
```

---

## 25.3 edges

```text
id
loop_id
source_node_id
target_node_id
relationship
reason
created_at
```

---

## 25.4 events

```text
id
source_app
event_type
external_id
timestamp
actor
subject
content
attachments JSONB
metadata JSONB
linked_loop_id
processed
```

Important unique constraint:

```text
(source_app, external_id)
```

when `external_id` is available.

This helps with webhook deduplication.

---

## 25.5 evidence

```text
id
node_id
event_id
relationship
confidence
reason
extracted_fields JSONB
verified
created_at
```

---

## 25.6 actions

```text
id
loop_id
node_id
app
action_type
parameters JSONB
risk_level
requires_approval
status
idempotency_key
external_id
verification_method
error
created_at
updated_at
```

Important unique constraint:

```text
idempotency_key
```

---

# 26. Idempotency Rules

This section is mandatory for the integrations/runtime teammate.

External event systems may deliver the same event more than once.

LoopGraph must not duplicate side effects.

---

## 26.1 Event Deduplication

When receiving an event:

```text
source_app + external_id
```

should uniquely identify it whenever possible.

If already processed:

```text
return existing event
do not process again
```

---

## 26.2 Action Idempotency

Each proposed external action must have an `idempotency_key`.

Examples:

```text
loop_refund_001:return_deadline

loop_refund_001:refund_checkpoint:2026-09-21

loop_presentation_001:sarah_followup:1
```

Before executing:

```text
if action with same idempotency_key is VERIFIED:
    do nothing
```

---

## 26.3 Replanning Idempotency

The Replanner should avoid repeatedly adding the same replacement node.

Example:

If Sarah → Mike reassignment is already applied:

```text
do not add another Mike node
```

Graph operations should validate current state before mutation.

---

# 27. State Transition Rules

State transitions should be explicit.

---

## 27.1 Node

Allowed examples:

```text
PENDING → ACTIVE

ACTIVE → WAITING

WAITING → VERIFIED

WAITING → BLOCKED

WAITING → FAILED

ACTIVE → SUPERSEDED

BLOCKED → ACTIVE

ANY NON-FINAL → CANCELLED
```

Avoid arbitrary transitions such as:

```text
VERIFIED → ACTIVE
```

unless an explicit contradiction invalidates prior evidence.

If verified evidence becomes invalid:

```text
VERIFIED → ACTIVE
```

may occur only through a recorded repair operation.

---

## 27.2 Action

Typical:

```text
PROPOSED
→ AWAITING_APPROVAL
→ APPROVED
→ EXECUTING
→ EXECUTED
→ VERIFIED
```

Autonomous low-risk action:

```text
PROPOSED
→ EXECUTING
→ EXECUTED
→ VERIFIED
```

Failure:

```text
EXECUTING → FAILED
```

---

# 28. Error Contract

All APIs should return consistent error payloads.

Recommended format:

```json
{
  "error": {
    "code": "ACTION_EXECUTION_FAILED",
    "message": "Failed to create Calendar event.",
    "details": {
      "action_id": "action_return_deadline"
    }
  }
}
```

Common error codes:

```text
VALIDATION_ERROR
LOOP_NOT_FOUND
NODE_NOT_FOUND
EVENT_DUPLICATE
ACTION_DUPLICATE
ACTION_EXECUTION_FAILED
ACTION_VERIFICATION_FAILED
APPROVAL_REQUIRED
INVALID_STATE_TRANSITION
INTEGRATION_UNAVAILABLE
LLM_OUTPUT_INVALID
```

---

# 29. LLM Structured Output Rules

Every LLM component must produce structured output validated with Pydantic.

Do not parse prose with regex.

Required pattern:

```text
LLM
 ↓
Structured JSON
 ↓
Pydantic validation
 ↓
Business-rule validation
 ↓
Persist / Execute
```

If validation fails:

```text
retry once with validation errors
```

If the second attempt fails:

```text
record LLM_OUTPUT_INVALID
do not execute side effects
```

---

# 30. Business-Rule Validation

Pydantic validates shape.

Business logic must validate meaning.

Examples:

### Outcome Compiler

Reject graph if:

```text
root node missing

dependency references unknown node

cycle exists where not intentionally supported

action references missing node

evidence requirement has no source app
```

### Replanner

Reject operation if:

```text
SUPERSEDE_NODE target does not exist

UPDATE_DEADLINE has invalid date

ADD_EDGE references missing nodes

CANCEL_ACTION targets VERIFIED action
```

---

# 31. Graph Validation

Before saving a compiled or modified graph:

```text
1. Every edge references valid nodes.
2. Root node exists.
3. Dependencies are resolvable.
4. No accidental self-dependencies.
5. No duplicate semantic edges.
6. Every actionable node has either:
   - evidence requirement
   - or explicit human/manual completion requirement.
```

For the hackathon, prefer a DAG unless a loop is intentionally represented through runtime retries rather than literal cyclic edges.

---

# 32. Module Ownership Boundaries

Recommended ownership:

## Intelligence Teammate

Owns:

```text
Outcome Compiler
Evidence Verifier prompts
Replanner
structured outputs
graph semantic validation
```

Consumes:

```text
Event
Loop
OutcomeNode
EvidenceRequirement
```

Returns:

```text
CompiledGraph
VerifyEventResponse
ReplanResponse
```

Must not directly call external applications.

---

## Integrations Teammate

Owns:

```text
Composio
Gmail
Slack
Drive
Calendar
webhooks
Action Executor
Action Verification
```

Consumes:

```text
Action
```

Returns:

```text
Event
ExecuteActionResponse
VerifyActionResponse
```

Must not invent graph reasoning.

---

## Runtime / Reliability Teammate

Owns:

```text
Supabase persistence
Event Router
Scheduler
state transitions
idempotency
graph operation application
activity log
LangGraph orchestration
```

Consumes all contracts.

---

## Frontend Teammate

Owns:

```text
Next.js
React Flow
Loop detail UI
activity feed
evidence panel
approval panel
realtime updates
```

Consumes:

```text
Loop Detail DTO
Approval objects
ActivityLog objects
```

Must not implement business state transitions in the browser.

---

# 33. Suggested Backend Service Interfaces

The exact class names may vary, but responsibilities should map approximately to:

```python
class OutcomeCompiler:
    async def compile(
        self,
        request: CompileGoalRequest
    ) -> CompiledGraph:
        ...
```

```python
class EventRouter:
    async def route(
        self,
        event: Event
    ) -> RouteEventResponse:
        ...
```

```python
class EvidenceVerifier:
    async def verify(
        self,
        request: VerifyEventRequest
    ) -> VerifyEventResponse:
        ...
```

```python
class Replanner:
    async def replan(
        self,
        request: ReplanRequest
    ) -> ReplanResponse:
        ...
```

```python
class ActionExecutor:
    async def execute(
        self,
        action: Action
    ) -> ExecuteActionResponse:
        ...
```

```python
class ActionVerifier:
    async def verify(
        self,
        action: Action,
        execution: ExecuteActionResponse
    ) -> VerifyActionResponse:
        ...
```

---

# 34. Generic Event Processing Pipeline

All app events should enter the same flow.

```text
External event
      ↓
Webhook / Scheduler / User input
      ↓
Normalize to Event
      ↓
Deduplicate
      ↓
Persist Event
      ↓
Route to loop(s)
      ↓
Evidence Verifier
      ↓
┌───────────────┬────────────────┐
│               │                │
PROVES       SUPERSEDES      UNRELATED
│               │                │
Update node     Replanner        Stop
│               │
│          Graph Operations
│               │
└───────┬───────┘
        ↓
Recalculate active nodes
        ↓
Generate proposed actions
        ↓
Risk policy
        ↓
Execute / Approval
        ↓
Verify external action
        ↓
Persist
        ↓
Realtime UI update
```

---

# 35. Loop Creation Pipeline

```text
User goal / incoming obligation
        ↓
Outcome Compiler
        ↓
Pydantic validation
        ↓
Graph validation
        ↓
Persist Loop + Nodes + Edges
        ↓
Persist proposed actions
        ↓
Execute autonomous actions
        ↓
Request approvals
        ↓
Frontend receives realtime graph
```

---

# 36. Replanning Pipeline

```text
Changed event / missed deadline / contradiction
        ↓
Verifier flags requires_replan
        ↓
Replanner returns operations
        ↓
Validate operations
        ↓
Apply operations transactionally
        ↓
Cancel stale actions
        ↓
Create new actions
        ↓
Activity log entry
        ↓
Realtime UI update
```

Graph changes should ideally be applied inside one database transaction.

---

# 37. Demo-Specific Contracts

For hackathon speed, support manual event injection using the same `Event` model.

This allows the team to simulate:

```text
refund processed

Sarah says Mike has final file

new insurance policy received

landlord requests declaration page
```

without creating separate demo-only code paths.

The demo injection endpoint must feed the normal event pipeline.

That ensures the demonstration uses the real system.

---

# 38. Minimum Required Unit Tests

Each team area should implement at least the following.

## Compiler

```text
valid goal → valid graph

missing root → rejected

bad dependency → rejected
```

## Event Router

```text
matching order number → refund loop

unrelated message → no match
```

## Evidence Verifier

```text
correct refund confirmation → PROVES

wrong order → INSUFFICIENT/UNRELATED

draft presentation → INSUFFICIENT
```

## Replanner

```text
Sarah → Mike message
→ supersede Sarah
→ add Mike
→ preserve final presentation node
```

## Action Executor

```text
duplicate idempotency key
→ no duplicate side effect
```

## Scheduler

```text
overdue waiting node
→ emits DEADLINE_REACHED once
```

---

# 39. Integration Contract Checklist

Before teammates integrate their branches, verify:

- [ ] Everyone uses the same enums.
- [ ] Everyone uses the same timestamp format.
- [ ] Everyone uses string IDs.
- [ ] Agent outputs validate through shared Pydantic models.
- [ ] UI consumes backend DTOs only.
- [ ] Integrations do not contain reasoning logic.
- [ ] Intelligence modules do not call apps directly.
- [ ] All actions have idempotency keys.
- [ ] All external events have deduplication identifiers where available.
- [ ] Replanner returns graph operations rather than regenerated graphs.
- [ ] Every state change emits an ActivityLog.
- [ ] `EXECUTED` and `VERIFIED` remain separate action states.
- [ ] `Action executed` and `Outcome completed` remain separate concepts.

---

# 40. Shared Source-of-Truth Rule

The final authoritative state is:

```text
Supabase / PostgreSQL
```

LangGraph checkpoints are execution/runtime state.

React state is presentation state.

Composio is integration state.

The frontend must never become authoritative.

The rule is:

```text
Supabase = product truth
```

---

# 41. Final Contract Summary

The complete LoopGraph system should communicate through this abstraction:

```text
            Event
              ↓
         Event Router
              ↓
     Evidence Verifier
              ↓
       Outcome Graph
              ↓
          Replanner
              ↓
            Action
              ↓
       Action Executor
              ↓
     Action Verification
              ↓
          Evidence
              ↓
      Outcome VERIFIED
```

The implementation can evolve during the hackathon.

These data contracts should change only when absolutely necessary and should be updated centrally when they do.

The objective is to allow four teammates to work simultaneously without coupling their internal code.
