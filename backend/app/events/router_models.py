"""Routing-shaped views of a loop, assembled directly from database rows.

Nothing here hydrates `Loop` or `OutcomeNode`. Those DTOs carry `node_ids` and
`depends_on`, which are derived fields that have to be rebuilt from the edges table to be
correct. Routing answers "which loop does this event touch", a question that never reads
graph topology, so paying for that reconstruction would be waste — and a half-populated
DTO is worse than no DTO, because it looks authoritative while lying about the graph.

Pure and synchronous: no I/O, no clock, no LLM.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final

from pydantic import AwareDatetime, Field

from app.agents.provider_schemas import ProviderModel
from app.constants import LoopStatus, NodeStatus
from app.graph.schemas import Confidence, ContractModel, JsonObject, NonEmpty

# A loop is only a routing candidate while it is still open for work. COMPLETED, FAILED
# and CANCELLED loops are terminal: a late event must never reopen one. Anything not
# listed here is excluded, so new LoopStatus members are opt-in rather than opt-out.
ROUTABLE_LOOP_STATUSES: Final[frozenset[LoopStatus]] = frozenset(
    {LoopStatus.ACTIVE, LoopStatus.WAITING, LoopStatus.BLOCKED}
)

# Nodes still awaiting an outcome. VERIFIED, FAILED, CANCELLED and SUPERSEDED nodes are
# settled, and harvesting identifiers from them would let a finished sub-goal keep
# attracting events.
OPEN_NODE_STATUSES: Final[frozenset[NodeStatus]] = frozenset(
    {NodeStatus.PENDING, NodeStatus.ACTIVE, NodeStatus.WAITING, NodeStatus.BLOCKED}
)

# The fields that pin a loop to a real-world thing. MUST STAY IN SYNC with the private
# `_IDENTITY` set in Teammate A's evidence validation module (currently
# `evidence_validation.py`, which filters node metadata through the same names when it
# computes `expected_fields`). If A adds a field there and not here, the router stops
# seeing a signal the verifier considers load-bearing. Neither side imports the other, so
# the only thing keeping them aligned is this comment.
IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
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
)

# The subset of IDENTITY_FIELDS whose values are opaque high-entropy tokens, so finding
# one verbatim in an event body is strong enough evidence to skip the semantic stage.
#
# Read this before adding to it. The exclusions are the whole point:
#
#   version, document_type — values are ordinary English. A loop expecting
#       version "final" would token-match "please send me the final version", and
#       document_type "policy" would match any sentence about an insurance policy.
#       Both would short-circuit routing on a word that proves nothing.
#   amount — numeric, so it goes through amount_match, not literal text matching.
#       An amount is also weak on its own: two unrelated $129.00 charges are common.
#       It corroborates an order_id, it never stands alone.
#   insured_person, recipient, owner, sender, actor — personal names. Not unique to a
#       loop (one person appears across many) and they collide across users.
#
# Everything here is a merchant- or carrier-issued reference that is effectively unique.
TOKEN_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {"order_id", "policy_id", "document_id"}
)


class LoopSummary(ContractModel):
    """A loop flattened into exactly what routing needs, and nothing more.

    Deliberately flat: no nodes list, no edges, no graph. `identifiers` is the merged
    identity view across the loop's open nodes, `people` the humans currently on the hook.
    """

    loop_id: NonEmpty
    title: NonEmpty
    goal: NonEmpty
    status: LoopStatus
    updated_at: AwareDatetime
    open_node_titles: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    identifiers: JsonObject = Field(default_factory=dict)
    thread_ids: list[str] = Field(default_factory=list)

    def prompt_view(self) -> dict[str, Any]:
        """The subset safe to put in front of a model.

        Omits `thread_ids` and `status` and `updated_at`: a thread id is a deterministic
        join key that was already resolved before any prompt is built, and asking a model
        to reason about opaque strings like "1690000123.111" invites invented matches.
        `title` is dropped too because `goal` states the same thing in fuller form.
        """
        return {
            "loop_id": self.loop_id,
            "goal": self.goal,
            "open_nodes": list(self.open_node_titles),
            "people": list(self.people),
            "identifiers": dict(self.identifiers),
        }


# --------------------------------------------------------------------------------------
# Semantic stage — domain result
#
# The model never fills the shared `RouteEventResponse`. It fills these, and `router.py`
# converts in Python, so the wire contract owned by another teammate can never be shaped
# by model output.
# --------------------------------------------------------------------------------------


class SemanticCandidate(ContractModel):
    """One loop the model believes the event relates to."""

    loop_id: NonEmpty
    confidence: Confidence
    reason: NonEmpty


class SemanticRouteResult(ContractModel):
    """The semantic stage's answer to both questions.

    `is_new_obligation` is only meaningful when `candidates` is empty; an event that
    belongs to an existing loop is not also a new obligation. The validator enforces that
    rather than trusting the model to respect it.
    """

    candidates: list[SemanticCandidate] = Field(default_factory=list)
    is_new_obligation: bool = False
    obligation_reason: str | None = None


# --------------------------------------------------------------------------------------
# Semantic stage — provider DTOs
#
# `assert_closed_schema` rejects any field default and requires every property, so nothing
# below may have a default and optionality is an explicit `| None` the model must emit as
# null. These exist only to be parsed out of a model response and mapped across.
# --------------------------------------------------------------------------------------


class RouteCandidateDraft(ProviderModel):
    loop_id: NonEmpty
    confidence: Confidence
    reason: NonEmpty


class RouteDraft(ProviderModel):
    candidates: list[RouteCandidateDraft]
    is_new_obligation: bool
    obligation_reason: str | None


def map_route_draft(draft: RouteDraft) -> SemanticRouteResult:
    """Convert provider output into the domain result.

    Raises `ValueError` on a self-contradictory draft, which the reasoning boundary treats
    as a validation failure and feeds back for one repair attempt.
    """
    if draft.is_new_obligation and draft.candidates:
        raise ValueError(
            "is_new_obligation cannot be true when candidates were returned: "
            "an event that belongs to an existing loop is not a new obligation"
        )
    if draft.is_new_obligation and not (draft.obligation_reason or "").strip():
        raise ValueError("is_new_obligation requires a nonblank obligation_reason")

    loop_ids = [candidate.loop_id for candidate in draft.candidates]
    if len(loop_ids) != len(set(loop_ids)):
        raise ValueError("A loop may appear at most once in candidates")

    return SemanticRouteResult(
        candidates=[
            SemanticCandidate(
                loop_id=candidate.loop_id,
                confidence=candidate.confidence,
                reason=candidate.reason,
            )
            for candidate in draft.candidates
        ],
        is_new_obligation=draft.is_new_obligation,
        obligation_reason=draft.obligation_reason,
    )


def _identity_subset(fields: Mapping[str, Any] | None) -> dict[str, Any]:
    if not fields:
        return {}
    return {key: value for key, value in fields.items() if key in IDENTITY_FIELDS}


def build_loop_summary(
    loop_row: Mapping[str, Any],
    node_rows: Sequence[Mapping[str, Any]],
    requirement_rows: Sequence[Mapping[str, Any]],
    thread_ids: Sequence[str],
) -> LoopSummary:
    """Flatten one loop's rows into a `LoopSummary`.

    Identifiers and owners are harvested from OPEN nodes only, per `OPEN_NODE_STATUSES`.
    Node metadata and requirement `required_fields` are merged, both filtered to
    `IDENTITY_FIELDS`; on a key collision the requirement wins, because a requirement is
    the stricter contract — node metadata records what the compiler assumed, while a
    requirement records what must actually be true for the node to be satisfied.

    Requirements attached to settled nodes are skipped for the same reason their nodes are.
    Order is insertion order throughout, so summaries are byte-stable across runs.
    """
    open_nodes = [row for row in node_rows if row.get("status") in OPEN_NODE_STATUSES]
    open_node_ids = {row["id"] for row in open_nodes}

    open_node_titles: list[str] = []
    people: list[str] = []
    for row in open_nodes:
        title = row.get("title")
        if title and title not in open_node_titles:
            open_node_titles.append(title)
        owner = row.get("owner")
        if owner and owner not in people:
            people.append(owner)

    identifiers: dict[str, Any] = {}
    for row in open_nodes:
        identifiers.update(_identity_subset(row.get("metadata")))
    # Applied second so the requirement's value overwrites the node's on conflict.
    for row in requirement_rows:
        if row.get("node_id") in open_node_ids:
            identifiers.update(_identity_subset(row.get("required_fields")))

    return LoopSummary(
        loop_id=loop_row["id"],
        title=loop_row["title"],
        goal=loop_row["goal"],
        status=LoopStatus(loop_row["status"]),
        updated_at=loop_row["updated_at"],
        open_node_titles=open_node_titles,
        people=people,
        identifiers=identifiers,
        thread_ids=list(thread_ids),
    )
