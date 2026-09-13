# A4 — Evidence Verifier handoff

The verifier assesses one observed event against candidate outcome nodes. It
returns the existing `VerifyEventResponse`; it never changes nodes, marks a loop
complete, persists evidence, fetches attachments, or executes actions.

## Calling the service

```python
from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.agents.verifier import EvidenceVerifier
from app.config import Settings
from app.graph.schemas import VerifyEventRequest


async def assess_event(request: VerifyEventRequest):
    settings = Settings.from_env()
    async with OpenAIProvider(settings) as provider:
        verifier = EvidenceVerifier(ReasoningBoundary(provider, settings))
        return await verifier.verify(request)
```

The caller owns the provider lifecycle. `verify_with_metadata(request)` returns
per-call prompt version (`verifier-v1`), boundary version, attempts, actual model,
latency, and usage alongside the response. Inject a fake provider and clock for
offline tests or an explicit aware clock for reproducible replay. The default
clock assesses current validity at processing time, not at the event's old date.

Supply the authoritative Loop, any nonempty subset of its candidate nodes, ALL
requirements referenced by those nodes, and the Event. Other nodes need not be
loaded, but the Loop's node IDs must include all dependencies. An event may be
unlinked or linked to this loop; routing, user authorization, and deduplication
remain C's responsibility. Input is revalidated and copied before awaiting the
provider. The verifier returns exactly one decision per supplied node, including
UNRELATED decisions, and rejects duplicate or unknown IDs.

## Requirement semantics and integration decision

The shared response has one satisfaction boolean per node, without a
per-requirement result or prior evidence context. A4 therefore uses this
conservative policy:

- Every requirement on a candidate node must match the current event before
  `evidence_satisfies_requirement=true` is permitted. No cross-event aggregation.
- Within each requirement, every required field must match, and its source app
  must allow the actual Event source. Empty required_fields still needs semantic
  proof of the description and a supporting outcome citation. A node with no
  requirements cannot receive PROVES.
- `must_all_match=false` is rejected as `VerifierInputError` before any model
  call. The shared contract does not identify optional fields; treating all keys
  as alternatives could allow an amount match to override a wrong order.
- C must confirm this policy before integration. Supporting optional evidence
  or cumulative evidence will need explicit field roles/per-requirement results
  and prior-evidence context. A4 does not silently redefine the shared flag.

This is the remaining A4 coordination item. It does not prevent use of the MVP
fixtures, whose requirements all use `must_all_match=true`.

## Evidence and adapter input conventions

These conventions use the existing Event JSON maps; shared DTOs and SQL are
unchanged. B/C must populate reserved observation fields themselves, not copy
them from arbitrary webhook/message JSON or user-written claims.

| Location | Meaning |
|---|---|
| `event.attachments[].id` | Unique nonempty artifact identifier; `attachment_id` is an alias. If both are present they must agree. |
| `event.attachments[].extracted_text` | Actual text inspected by B's document adapter. A URL, filename, or download success is insufficient. |
| `event.attachments[].observed_fields` | Optional structured observations from that same artifact, such as version or policy dates; extraction must agree with supplied values. |
| `event.metadata.read_after_write_verified` | Literal boolean `true` only after B verifies the external state. Required for API_STATE proof; not a substitute for evidence criteria. |

For document proof, model output identifies one `attachment_id`, supplies
`document_type`, and cites that artifact's inspected text for document fields.
Identity, version, and validity cannot be assembled from different attachments.
File-backed Events from Drive should include the observed file in `attachments`
using this convention. A4 does not fetch a URL or open local file paths.

For current policy proof, extraction supplies `valid_from` and `valid_until`.
Both must be ISO dates (inclusive endpoints in the configured timezone) or both
aware ISO timestamps (inclusive start, exclusive end). Missing bounds, future
coverage, expired coverage, naive timestamps, and mixed formats fail validation.
An expected/extracted `expired=false` or `renewed=true` triggers this date check
for document evidence. Current validity uses the trusted boundary reference time.

EMAIL_CONFIRMATION and SLACK_CONFIRMATION proof requires MESSAGE_RECEIVED.
Sending a follow-up or submitting a policy does not prove an inbound refund or
landlord acknowledgement. API_STATE submission evidence also needs the inspected
attachment when `has_valid_attachment=true` is required. The model must assess
whether that document actually fulfills the submission criteria.

## Extraction checks and source citations

The new closed provider DTO `EvidenceVerificationDraft` adds typed citations to
the existing decision fields. A2's `VerifyDraft` and the shared response stay
unchanged. The adapter stores validated citations under
`decision.extracted_fields._citations` for audit; consumers should reserve this
key rather than treating it as an extracted business field.

Each citation identifies a field, source (`event.actor`, `event.subject`,
`event.content`, or `attachment`), attachment ID or null, and a verbatim quote.
PROVES needs an `$outcome` citation and citations for required fields. Quotes
must occur in the selected source. CONTRADICTS and SUPERSEDES also require an
outcome citation and `requires_replan=true`.

Required values are compared without truthy coercion: boolean true is not 1,
and numeric 129 is not the string "129". Numeric comparisons use decimal values
without tolerance. Currency, document type, and version allow case/outer-space
normalization; other strings and nested structures match exactly. Literal IDs,
recipients, sender/actor identities, and currency must occur in their source
quote; sender/actor constraints also match Event.actor. Node metadata identity
constraints are enforced alongside requirements. Supported metadata keys are
`order_id`, `amount`, `currency`, `policy_id`, `insured_person`, `recipient`,
`document_id`, `document_type`, `version`, `owner`, `sender`, and `actor`.
The node's `owner` property alone is not an asserted message sender.

These checks constrain the model's extraction. They do not prove that a quoted
passage entails the claimed outcome. In particular, interpretation of amounts,
document purpose, acknowledgement, negation, and mixed-message context remains
semantic work. Literal identity matches do not authenticate the sender. The
model must not copy expected fields into extraction when observations omit them.

## Errors and runtime application

Invalid context raises `VerifierInputError` (`code=VALIDATION_ERROR`) or a Pydantic
validation error before a model call. Invalid output, references, citations, or
proof gets one repair attempt; a second invalid result raises
`LLMError(code=LLM_OUTPUT_INVALID)`. Provider timeout, access, refusal, incomplete
generation, and transport errors retain the A2 error classifications. There is
no fallback that returns a failed proof as successful verification.

A valid PROVES decision can be returned for a BLOCKED node: evidence satisfaction
does not establish satisfied dependencies. C persists an assessed Evidence with
`verified=true` for any relationship, including INSUFFICIENT, and separately
enforces dependencies, active lifecycle, contradictions, verified required
actions, and all completion rules. Never treat `Evidence.verified` alone as node
completion. C may send changed evidence to A5; A4 only requests replanning.

## Validation performed

Offline tests exercise the three MVP scenarios and failure cases from V-01/02,
RR-05/06, PR-08, PW-02/03, and LLM-04: wrong order/amount/currency, draft versus
final, current versus expired/future policy, renewal without PDF, submission
without attachment/readback, acknowledgement versus a sent message, unrelated
events, source injection, invalid IDs/context, citations, retry, and input
immutability. An SDK test parses the real strict schema over mocked HTTP and
repairs an invalid relationship using low reasoning effort.

These are scripted contract and business-rule tests, not live semantic scores.
No live model or connected-app call was made for A4. Live extraction quality,
source-instruction resistance, adapter provenance, cumulative evidence, and
runtime integration remain evaluation/integration work.
