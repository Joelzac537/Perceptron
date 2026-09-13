"""The Event Router: given one normalized Event, which existing loop(s) does it affect?

Read-only. Never writes to the database, never calls Gmail/Slack/Drive/Calendar, never
mutates a loop or node, and never decides whether evidence proves anything — a match here
says "this event is about that loop", not "this event satisfies it". The Evidence Verifier
makes the second call.

Staged deliberately cheapest-first. Stage 0 touches nothing, Stage 1 is one or two indexed
reads plus regex. The semantic stage that consults a model lands in a later task; until
then an event that survives Stage 1 unmatched returns no matches rather than guessing.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from app.events.identifiers import (
    KEY_AMOUNT,
    amount_match,
    extract_identifiers,
    token_match,
)
from app.events.repository import LoopRepository
from app.events.router_models import TOKEN_IDENTITY_FIELDS, LoopSummary
from app.events.router_validation import validate_route_response
from app.graph.schemas import Event, LoopMatch, RouteEventRequest, RouteEventResponse

# --- Confidence policy ----------------------------------------------------------------
# Below this a candidate is discarded outright rather than passed on as a weak guess.
MATCH_FLOOR: Final = 0.50
# At or above this a candidate is strong enough to stand alone.
MATCH_CONFIDENT: Final = 0.80
# Two confident candidates this close together are treated as genuinely ambiguous and both
# are returned, rather than the router picking a winner it has no basis to pick.
AMBIGUITY_MARGIN: Final = 0.15
# At or above this, stop scoring and answer. Set above CONFIDENCE_ACTOR on purpose: an
# actor match must never short-circuit, because Sarah messaging about something unrelated
# to the loop she owns a node in is an ordinary Tuesday.
SHORT_CIRCUIT: Final = 0.90

# --- Per-signal confidences -----------------------------------------------------------
# A thread already linked to a loop is the strongest evidence short of an explicit link.
CONFIDENCE_THREAD: Final = 0.99
# An opaque merchant- or carrier-issued reference appearing verbatim in the event body.
CONFIDENCE_IDENTIFIER: Final = 0.97
# A person on the hook for an open node. Weak: names are not unique to a loop.
CONFIDENCE_ACTOR: Final = 0.55
# Amount never scores on its own, it only nudges a match that already fired.
AMOUNT_CORROBORATION_BONUS: Final = 0.02

# Confidence is a probability; nothing may exceed certainty.
MAX_CONFIDENCE: Final = 1.0
# Stage 0 is not an inference. The upstream connector already resolved the link.
CONFIDENCE_PRE_LINKED: Final = MAX_CONFIDENCE

# Scores are rounded before the policy runs so that 0.97 + 0.02 reads as 0.99 rather than
# 0.9899999999999999 on the wire.
CONFIDENCE_PRECISION: Final = 4
# Binary floating point makes 0.99 - 0.84 slightly larger than 0.15, which would drop a
# candidate that is exactly on the ambiguity boundary. Compare with a tolerance.
FLOAT_TOLERANCE: Final = 1e-9

# --- Where thread ids live in event metadata ------------------------------------------
# Gmail calls it thread_id, Slack calls it thread_ts. Both are checked; neither is assumed.
THREAD_METADATA_KEYS: Final = ("thread_id", "thread_ts")

# --- Reason templates -----------------------------------------------------------------
# LoopMatch.reason is NonEmpty, and a user eventually reads these, so every path builds a
# specific sentence naming the signal that fired.
REASON_PRE_LINKED: Final = (
    "Event arrived already linked to loop {loop_id}; routing was resolved upstream."
)
REASON_THREAD: Final = "Event thread {thread_id} is already linked to this loop."
REASON_IDENTIFIER: Final = "Event text contains this loop's {field} {value}."
# Stronger phrasing for when the event labelled the reference itself ("Order #A1298")
# rather than the string merely appearing somewhere in the body.
REASON_IDENTIFIER_LABELLED: Final = (
    "Event explicitly cites {field} {value}, matching this loop."
)
REASON_ACTOR: Final = "Event actor {actor} owns an open node in this loop."
REASON_AMOUNT_SUFFIX: Final = " Amount {amount} corroborates."


def _event_text(event: Event) -> str:
    """Subject and body as one searchable block, newline-joined so neither bleeds into the
    other and creates a token boundary that is not really there."""
    return "\n".join(part for part in (event.subject, event.content) if part)


def _event_thread_ids(event: Event) -> list[str]:
    """Every thread identifier the event carries, in `THREAD_METADATA_KEYS` order."""
    found: list[str] = []
    for key in THREAD_METADATA_KEYS:
        value = event.metadata.get(key)
        if isinstance(value, str) and value.strip() and value not in found:
            found.append(value)
    return found


def _is_number(value: Any) -> bool:
    # bool is a subclass of int; an amount of True is a data bug, not a number.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class EventRouter:
    """Routes one event to the loops it affects.

    `user_id` is a constructor argument because `Event` has no `user_id` field and
    `RouteEventRequest` carries only the event. That is a known gap in the shared contract
    owned by another teammate, not something to infer or paper over: without it every
    repository read would be untenanted, and the router would happily match one person's
    event to another person's loop. Making it explicit here keeps the gap visible until the
    contract is amended.

    `now_fn` is held rather than called for now — Stage 0 and Stage 1 are time-independent.
    The semantic stage will use it for recency weighting, and taking it as an injected
    callable now means no logic in this module ever reaches for the wall clock itself.
    """

    def __init__(
        self,
        repo: LoopRepository,
        llm: Any,
        user_id: str,
        now_fn: Callable[[], datetime],
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._user_id = user_id
        self._now_fn = now_fn

    async def route(self, request: RouteEventRequest) -> RouteEventResponse:
        event = request.event

        # --- STAGE 0: already linked. No database, no model. --------------------------
        if event.linked_loop_id:
            linked_id = event.linked_loop_id
            response = RouteEventResponse(
                matches=[
                    LoopMatch(
                        loop_id=linked_id,
                        confidence=CONFIDENCE_PRE_LINKED,
                        reason=REASON_PRE_LINKED.format(loop_id=linked_id),
                    )
                ],
                create_new_loop_candidate=False,
            )
            # The candidate set is the link itself; the repository was never consulted.
            return validate_route_response(response, {linked_id})

        # --- STAGE 1: deterministic signals. Database reads, still no model. ----------
        summaries = await self._repo.list_routable_loops(self._user_id)
        by_id = {summary.loop_id: summary for summary in summaries}
        if not by_id:
            return validate_route_response(
                RouteEventResponse(matches=[], create_new_loop_candidate=False), ()
            )

        scores: dict[str, float] = {}
        reasons: dict[str, str] = {}
        # Loops that fired on thread or identifier. Only these are eligible for the amount
        # bonus; an actor-only match has not earned corroboration.
        corroboratable: set[str] = set()

        def record(loop_id: str, confidence: float, reason: str) -> None:
            """Keep the highest signal that applies to a loop, and the reason that goes
            with it."""
            if confidence > scores.get(loop_id, 0.0):
                scores[loop_id] = confidence
                reasons[loop_id] = reason

        # (a) THREAD. find_loop_ids_by_thread returns dict[str, list[str]]: one thread can
        # legitimately map to several loops, and every one of them is a real candidate.
        thread_ids = _event_thread_ids(event)
        if thread_ids:
            linked = await self._repo.find_loop_ids_by_thread(self._user_id, thread_ids)
            for thread_id, loop_ids in linked.items():
                for loop_id in loop_ids:
                    # Both reads apply the same tenant and status filters, so this should
                    # always hold; skipping rather than trusting it keeps a repository bug
                    # from leaking a loop the router never considered.
                    if loop_id in by_id:
                        record(
                            loop_id,
                            CONFIDENCE_THREAD,
                            REASON_THREAD.format(thread_id=thread_id),
                        )
                        corroboratable.add(loop_id)

        # (b) TOKEN IDENTIFIER. The decision is made with token_match against the loop's
        # stored value, not by comparing extracted values, because token_match is strictly
        # the more permissive of the two: an extracted value is by definition a substring
        # of the text, so anything extraction agrees on token_match already catches, and
        # matching this way additionally survives an event that states a bare reference
        # with no "Order"/"Policy" keyword for extraction to anchor on.
        #
        # extract_identifiers still runs, and is what distinguishes an event that labelled
        # its reference ("Order #A1298") from one where the string merely appears. Both
        # score the same, but the reason a user reads should not claim more than happened.
        text = _event_text(event)
        event_identifiers = extract_identifiers(text)
        if text:
            for loop_id, summary in by_id.items():
                # Sorted so the reason is stable when a loop carries more than one token.
                for field in sorted(TOKEN_IDENTITY_FIELDS):
                    value = summary.identifiers.get(field)
                    if value is None or not token_match(str(value), text):
                        continue
                    labelled = token_match(
                        str(value), str(event_identifiers.get(field, ""))
                    )
                    template = REASON_IDENTIFIER_LABELLED if labelled else REASON_IDENTIFIER
                    record(
                        loop_id,
                        CONFIDENCE_IDENTIFIER,
                        template.format(field=field, value=value),
                    )
                    corroboratable.add(loop_id)
                    break

        # (c) ACTOR. Deliberately below SHORT_CIRCUIT.
        actor = (event.actor or "").strip()
        if actor:
            folded = actor.casefold()
            for loop_id, summary in by_id.items():
                if any(folded == person.strip().casefold() for person in summary.people):
                    record(loop_id, CONFIDENCE_ACTOR, REASON_ACTOR.format(actor=actor))

        # AMOUNT: corroboration only. A loop that matched on amount alone scores nothing,
        # because amount_match accepts bare numbers and "129 Main Street" must never route
        # an event.
        for loop_id in corroboratable:
            amount = by_id[loop_id].identifiers.get(KEY_AMOUNT)
            if _is_number(amount) and amount_match(float(amount), text):
                scores[loop_id] = min(
                    scores[loop_id] + AMOUNT_CORROBORATION_BONUS, MAX_CONFIDENCE
                )
                reasons[loop_id] += REASON_AMOUNT_SUFFIX.format(amount=amount)

        scores = {
            loop_id: round(score, CONFIDENCE_PRECISION) for loop_id, score in scores.items()
        }

        # Short circuit. Returns every loop at or above the bar, not just one: a thread
        # shared by two of this user's loops puts both at CONFIDENCE_THREAD, and dropping
        # one would silently lose a live candidate.
        short_circuited = [
            loop_id for loop_id, score in scores.items() if score >= SHORT_CIRCUIT
        ]
        if short_circuited:
            return self._respond(short_circuited, scores, reasons, by_id)

        kept = self._apply_confidence_policy(scores)
        return self._respond(kept, scores, reasons, by_id)

    @staticmethod
    def _apply_confidence_policy(scores: dict[str, float]) -> list[str]:
        """Decide which scored loops survive.

        Anything under MATCH_FLOOR is dropped. If the best candidate is confident, only
        confident candidates within AMBIGUITY_MARGIN of it are returned — which yields a
        single match when one loop clearly leads, and the full tied set when several are
        genuinely indistinguishable. If nothing reached MATCH_CONFIDENT, every candidate
        above the floor is returned and the Verifier gets to say UNRELATED.
        """
        candidates = [loop_id for loop_id, score in scores.items() if score >= MATCH_FLOOR]
        if not candidates:
            return []

        top = max(scores[loop_id] for loop_id in candidates)
        if top < MATCH_CONFIDENT:
            return candidates
        return [
            loop_id
            for loop_id in candidates
            if scores[loop_id] >= MATCH_CONFIDENT
            and top - scores[loop_id] <= AMBIGUITY_MARGIN + FLOAT_TOLERANCE
        ]

    def _respond(
        self,
        loop_ids: list[str],
        scores: dict[str, float],
        reasons: dict[str, str],
        by_id: dict[str, LoopSummary],
    ) -> RouteEventResponse:
        """Order, build and validate the response.

        Sorted by confidence descending, then loop_id ascending so a tie is stable across
        runs. `matches` is always passed explicitly even when empty, since
        RouteEventResponse.matches has no default.
        """
        ordered = sorted(loop_ids, key=lambda loop_id: (-scores[loop_id], loop_id))
        response = RouteEventResponse(
            matches=[
                LoopMatch(
                    loop_id=loop_id,
                    confidence=scores[loop_id],
                    reason=reasons[loop_id],
                )
                for loop_id in ordered
            ],
            # An event that belongs to an existing loop is never also a new obligation.
            # The empty-matches case stays False until the semantic stage lands.
            create_new_loop_candidate=False,
        )
        return validate_route_response(response, by_id.keys())
