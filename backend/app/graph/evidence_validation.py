"""Deterministic constraints on semantic evidence extraction, not node transitions."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, TypeAdapter

from app.agents.verifier_schemas import EvidenceVerificationDraft
from app.constants import EvidenceRelationship as Relationship
from app.graph.schemas import VerifyEventRequest, VerifyEventResponse

_AWARE = TypeAdapter(AwareDatetime)
# These identity constraints may also be carried on a node by the compiler.
_IDENTITY = {
    "order_id",
    "amount",
    "currency",
    "policy_id",
    "insured_person",
    "recipient",
    "document_id",
    "document_type",
    "version",
    "owner",
    "sender",
    "actor",
}
_DOCUMENT_FIELDS = {
    "document_type",
    "document_id",
    "policy_id",
    "insured_person",
    "version",
    "renewed",
    "expired",
    "valid_from",
    "valid_until",
}
_LITERAL_FIELDS = {
    "order_id",
    "policy_id",
    "document_id",
    "recipient",
    "sender",
    "actor",
    "currency",
}


class VerifierInputError(ValueError):
    code = "VALIDATION_ERROR"


def validate_verifier_request(request: VerifyEventRequest) -> None:
    def require(condition, message):
        if not condition:
            raise VerifierInputError(message)

    loop = request.loop
    node_ids = [node.id for node in request.nodes]
    requirement_ids = [req.id for req in request.requirements]
    require(bool(node_ids), "Supply at least one candidate node")
    require(len(node_ids) == len(set(node_ids)), "Duplicate candidate nodes")
    require(len(loop.node_ids) == len(set(loop.node_ids)), "Duplicate loop node IDs")
    require(loop.root_node_id in loop.node_ids, "Root must belong to the loop")
    require(set(node_ids) <= set(loop.node_ids), "Candidate nodes must belong to the loop")
    require(len(requirement_ids) == len(set(requirement_ids)), "Duplicate requirements")
    require(
        request.event.linked_loop_id in (None, loop.id),
        "Event belongs to a different loop",
    )
    for node in request.nodes:
        require(node.loop_id == loop.id, "Node belongs to a different loop")
        require(
            len(node.depends_on) == len(set(node.depends_on))
            and node.id not in node.depends_on
            and set(node.depends_on) <= set(loop.node_ids),
            "Invalid node dependencies",
        )
        require(
            len(node.evidence_requirement_ids) == len(set(node.evidence_requirement_ids)),
            "Duplicate node requirement references",
        )
        require(
            set(node.evidence_requirement_ids)
            == {req.id for req in request.requirements if req.node_id == node.id},
            "Supply every requirement for each candidate node",
        )
    for requirement in request.requirements:
        require(requirement.node_id in node_ids, "Requirement belongs to an unknown node")
        require(
            requirement.must_all_match,
            "must_all_match=false needs an explicit optional-field policy agreed with runtime",
        )
    attachment_ids = [
        item.get("id", item.get("attachment_id")) for item in request.event.attachments
    ]
    supplied_ids = [value for value in attachment_ids if value is not None]
    require(
        all(isinstance(value, str) and value.strip() for value in supplied_ids),
        "Attachment IDs must be nonempty strings",
    )
    require(len(supplied_ids) == len(set(supplied_ids)), "Duplicate attachment IDs")
    for item in request.event.attachments:
        require(
            not ("id" in item and "attachment_id" in item) or item["id"] == item["attachment_id"],
            "Conflicting attachment IDs",
        )


def _matches(actual, expected, key: str) -> bool:
    if isinstance(expected, bool) or expected is None:
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, (int, float)):
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and Decimal(str(actual)) == Decimal(str(expected))
        )
    if isinstance(expected, str):
        if not isinstance(actual, str):
            return False
        if key in {"currency", "document_type", "version"}:
            return actual.strip().casefold() == expected.strip().casefold()
        return actual == expected
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and all(_matches(actual[k], value, k) for k, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_matches(a, b, key) for a, b in zip(actual, expected, strict=True))
        )
    return False


def _in_period(fields: dict, now: datetime, timezone: str) -> bool:
    """Date bounds include the last local day; timestamp end bounds are exclusive."""
    try:
        start, end = fields["valid_from"], fields["valid_until"]
        if not isinstance(start, str) or not isinstance(end, str):
            return False
        if len(start) == 10 and len(end) == 10:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            return first <= now.astimezone(ZoneInfo(timezone)).date() <= last
        return _AWARE.validate_python(start) <= now < _AWARE.validate_python(end)
    except (KeyError, ValueError):
        return False


def _contains_identifier(value: str, quote: str) -> bool:
    """Check literal source grounding, not semantic interpretation of the sentence."""
    start = 0
    while (offset := quote.find(value, start)) >= 0:
        end = offset + len(value)
        before = quote[offset - 1] if offset else ""
        after = quote[end] if end < len(quote) else ""
        if not (before and (before.isalnum() or before in "_@+.-")) and not (
            after
            and (
                after.isalnum()
                or after in "_@+-"
                or (after == "." and end + 1 < len(quote) and quote[end + 1].isalnum())
            )
        ):
            return True
        start = offset + 1
    return False


def validate_evidence_result(
    response: VerifyEventResponse,
    request: VerifyEventRequest,
    draft: EvidenceVerificationDraft,
    now: datetime,
    timezone: str,
) -> None:
    """Raise for unsupported proof so the A2 boundary permits one repair attempt."""
    errors = []
    if {d.node_id for d in response.decisions} != {n.id for n in request.nodes}:
        errors.append("Return exactly one decision for each candidate node, including unrelated")
    nodes = {node.id: node for node in request.nodes}
    attachments = {
        item.get("id", item.get("attachment_id")): item
        for item in request.event.attachments
        if isinstance(item.get("id", item.get("attachment_id")), str)
    }
    for decision, source in zip(response.decisions, draft.decisions, strict=True):
        fields = decision.extracted_fields
        if "_citations" in fields:
            errors.append("_citations is reserved for validated citation records")
        citations = {}
        for citation in source.citations:
            if citation.field in citations:
                errors.append("Duplicate field citation")
            citations[citation.field] = citation
            if citation.field not in fields and citation.field != "$outcome":
                errors.append("Citation must refer to an extracted field or $outcome")
            if citation.source == "attachment":
                text = attachments.get(citation.attachment_id, {}).get("extracted_text")
            else:
                text = getattr(request.event, citation.source.split(".")[1])
                if citation.attachment_id is not None:
                    errors.append("Event citations cannot reference an attachment")
            if not isinstance(text, str) or citation.quote not in text:
                errors.append("Citation quote must occur verbatim in the supplied source content")
        if decision.relationship in {Relationship.CONTRADICTS, Relationship.SUPERSEDES}:
            if not response.requires_replan:
                errors.append("Contradiction or supersession must request replanning")
            if "$outcome" not in citations:
                errors.append("Contradiction or supersession needs an $outcome citation")
        if decision.relationship != Relationship.PROVES:
            continue
        if not decision.evidence_satisfies_requirement:
            errors.append("Use PARTIALLY_SUPPORTS or INSUFFICIENT when proof is incomplete")
        if "$outcome" not in citations:
            errors.append("PROVES needs an $outcome citation supporting the actual outcome")
        requirements = [r for r in request.requirements if r.node_id == decision.node_id]
        if not requirements:
            errors.append("A node without evidence requirements cannot be proved")
        expected_fields = {
            key: value
            for key, value in nodes[decision.node_id].metadata.items()
            if key in _IDENTITY
        }
        for requirement in requirements:
            if request.event.source_app not in requirement.source_apps:
                errors.append("Event source app cannot satisfy this requirement")
            if (
                requirement.type == "API_STATE"
                and request.event.metadata.get("read_after_write_verified") is not True
            ):
                errors.append("API_STATE proof requires adapter read-after-write verification")
            if requirement.type in {"EMAIL_CONFIRMATION", "SLACK_CONFIRMATION"} and (
                request.event.event_type != "MESSAGE_RECEIVED"
            ):
                errors.append("Message confirmation requires an inbound observed message")
            # Compare each requirement separately; conflicting requirements cannot overwrite.
            for key, expected in (expected_fields | requirement.required_fields).items():
                if key not in fields or not _matches(fields[key], expected, key):
                    errors.append(f"Required field {key} is missing or mismatched")
                if key not in citations:
                    errors.append(f"Required field {key} needs source support")
                elif key in _LITERAL_FIELDS and isinstance(fields.get(key), str):
                    value, quote = fields[key], citations[key].quote
                    if key == "currency":
                        value, quote = value.upper(), quote.upper()
                    if not value or not _contains_identifier(value, quote):
                        errors.append(f"Identity field {key} must occur in its source quote")
                if key in {"sender", "actor"} and fields.get(key) != request.event.actor:
                    errors.append(f"Identity field {key} must match the observed event actor")
            for key in expected_fields.keys() & requirement.required_fields.keys():
                if not _matches(requirement.required_fields[key], expected_fields[key], key):
                    errors.append(f"Requirement conflicts with node identity field {key}")
            needs_document = (
                requirement.type in {"DOCUMENT_RECEIVED", "DOCUMENT_VALID"}
                or requirement.required_fields.get("has_valid_attachment") is True
                or "document_type" in requirement.required_fields
            )
            if needs_document:
                attachment = (
                    attachments.get(fields.get("attachment_id"))
                    if isinstance(fields.get("attachment_id"), str)
                    else None
                )
                if (
                    not attachment
                    or not isinstance(attachment.get("extracted_text"), str)
                    or not attachment["extracted_text"].strip()
                ):
                    errors.append(
                        "Document proof requires an identified attachment with inspected text"
                    )
                    continue
                document_keys = _DOCUMENT_FIELDS & fields.keys()
                if not isinstance(fields.get("document_type"), str) or not fields["document_type"]:
                    errors.append("Extract document identity from the inspected attachment")
                for key in document_keys:
                    citation = citations.get(key)
                    if (
                        not citation
                        or citation.source != "attachment"
                        or citation.attachment_id != fields["attachment_id"]
                    ):
                        errors.append(f"Document field {key} must cite the selected attachment")
                # Adapter observations constrain extraction; filenames do not establish content.
                observed = attachment.get("observed_fields", {})
                if not isinstance(observed, dict):
                    errors.append("Attachment observed_fields must be an object")
                else:
                    for key in fields.keys() & observed.keys():
                        if not _matches(fields[key], observed[key], key):
                            errors.append(
                                f"Extracted field {key} conflicts with attachment observation"
                            )
                if (
                    requirement.required_fields.get("expired") is False
                    or requirement.required_fields.get("renewed") is True
                    or fields.get("expired") is False
                    or fields.get("renewed") is True
                ):
                    if not _in_period(fields, now, timezone):
                        errors.append(
                            "Current proof needs a valid coverage period containing reference_time"
                        )
    if errors:
        raise ValueError("; ".join(dict.fromkeys(errors)))
