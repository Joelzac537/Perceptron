"""The Event Router: given one normalized Event, which existing loop(s) does it affect?

Read-only. Never writes to the database, never calls Gmail/Slack/Drive/Calendar, never
mutates a loop or node, and never decides whether evidence proves anything — a match here
says "this event is about that loop", not "this event satisfies it". The Evidence Verifier
makes the second call.

Staged deliberately cheapest-first. Stage 0 touches nothing, Stage 1 is one or two indexed
reads plus regex, and only an event that survives both without a confident answer reaches
Stage 2, which consults a model. Stage 3 asks that same model whether an unmatched event
is a new obligation — one call answers both questions.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel

from app.agents.llm import LLMError, Prompt, ReasoningBoundary
from app.config import Settings
from app.events.identifiers import (
    KEY_AMOUNT,
    amount_match,
    extract_identifiers,
    token_match,
)
from app.events.repository import LoopRepository
from app.events.router_models import (
    TOKEN_IDENTITY_FIELDS,
    LoopSummary,
    RouteDraft,
    SemanticRouteResult,
    map_route_draft,
)
from app.events.router_validation import validate_route_response
from app.graph.schemas import Event, LoopMatch, RouteEventRequest, RouteEventResponse
from app.prompts.router import ROUTER_VERSION, router_prompt

# The repo's convention is to return diagnostics on LLMError rather than log them. The
# router is the one place that swallows the error instead of raising, so the failure would
# otherwise vanish silently. Only the code and per-attempt outcomes are ever emitted —
# never the envelope, request body, or settings, per the LLMError contract.
logger = logging.getLogger(__name__)

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

# --- Semantic stage -------------------------------------------------------------------
# A prompt listing every open loop would grow without bound and bury the real candidate in
# noise. Ten most-recently-updated loops is the cap; beyond that, recency is the best
# available proxy for relevance.
SEMANTIC_CANDIDATE_CAP: Final = 10
# The 60s Settings default is sized for the compiler, which builds a whole graph. Routing
# is one short judgement sitting on the critical path of every ingested event, so a stalled
# model must not stall ingestion.
ROUTER_TIMEOUT_SECONDS: Final = 15.0

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


class EventSummary(BaseModel):
    """The event as the model sees it.

    A projection rather than the whole `Event`: ids, `processed`, and `linked_loop_id`
    are routing bookkeeping that would only invite the model to reason about them.
    """

    source_app: str
    event_type: str
    timestamp: str
    actor: str | None
    subject: str | None
    content: str | None
    attachment_count: int


class SemanticRouteInput(BaseModel):
    """The single request model handed to `ReasoningBoundary.run`.

    `run` takes exactly one `BaseModel`, so the event and its candidates are folded
    together here — the same shape `RepairInput` uses for the replanner.
    """

    event: EventSummary
    candidates: list[dict[str, Any]]


def _summarize_event(event: Event) -> EventSummary:
    return EventSummary(
        source_app=event.source_app,
        event_type=event.event_type,
        timestamp=event.timestamp.isoformat(),
        actor=event.actor,
        subject=event.subject,
        content=event.content,
        attachment_count=len(event.attachments),
    )


def _prompt_candidates(summaries: Sequence[LoopSummary]) -> list[dict[str, Any]]:
    """The most recently updated loops, capped, as prompt views.

    `prompt_view()` already withholds thread ids: they are deterministic join keys that
    Stage 1 has finished with, and opaque strings in a prompt invite invented matches.
    """
    ordered = sorted(summaries, key=lambda summary: summary.updated_at, reverse=True)
    return [summary.prompt_view() for summary in ordered[:SEMANTIC_CANDIDATE_CAP]]


class EventRouter:
    """Routes one event to the loops it affects.

    `user_id` is a constructor argument because `Event` has no `user_id` field and
    `RouteEventRequest` carries only the event. That is a known gap in the shared contract
    owned by another teammate, not something to infer or paper over: without it every
    repository read would be untenanted, and the router would happily match one person's
    event to another person's loop. Making it explicit here keeps the gap visible until the
    contract is amended.

    `llm` is a `StructuredProvider` whose lifecycle the caller owns and closes, matching
    the convention every agent handoff doc states. `now_fn` becomes the reasoning
    boundary's clock, so `reference_time` and `timezone` reach the model through the
    trusted envelope and nothing in this module ever reads the wall clock itself.

    `settings` is optional and only shapes the model call; the timeout is overridden to
    `ROUTER_TIMEOUT_SECONDS` regardless of what is passed in.
    """

    def __init__(
        self,
        repo: LoopRepository,
        llm: Any,
        user_id: str,
        now_fn: Callable[[], datetime],
        settings: Settings | None = None,
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._user_id = user_id
        self._now_fn = now_fn
        # Settings is frozen, so derive rather than mutate.
        self._settings = (settings or Settings()).model_copy(
            update={"timeout_seconds": ROUTER_TIMEOUT_SECONDS}
        )

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
        # No early return when by_id is empty. Every Stage 1 signal below simply finds
        # nothing to score, and Stage 2/3 still has to run: with zero tracked loops the
        # match question is trivially "none", but the new-obligation question is the
        # whole point. Returning here would make the first event of a user's life
        # unable to create a loop, and therefore every event after it too.

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

        # --- STAGE 2/3: semantic judgement. One model call answers both questions. -----
        semantic = await self._ask_model(event, list(by_id.values()))
        if semantic is None:
            # The model failed twice. A routing failure returns nothing; it must never
            # invent a loop, propose a new one, or raise into the caller.
            return validate_route_response(
                RouteEventResponse(matches=[], create_new_loop_candidate=False), by_id.keys()
            )

        for candidate in semantic.candidates:
            # Filter to the candidate set: a model naming a loop it was not offered is a
            # hallucination, not a match.
            if candidate.loop_id not in by_id:
                continue
            # Merge rather than replace, so a weak deterministic signal is not discarded
            # when the model independently agrees about the same loop.
            record(candidate.loop_id, candidate.confidence, candidate.reason)

        scores = {
            loop_id: round(score, CONFIDENCE_PRECISION) for loop_id, score in scores.items()
        }
        kept = self._apply_confidence_policy(scores)
        return self._respond(
            kept,
            scores,
            reasons,
            by_id,
            new_loop_candidate=semantic.is_new_obligation,
        )

    async def _ask_model(
        self, event: Event, summaries: Sequence[LoopSummary]
    ) -> SemanticRouteResult | None:
        """Run the semantic stage, or return None if the model could not be trusted.

        `ReasoningBoundary` already implements the validate-then-repair-once contract: a
        first invalid result is fed back as `validation_feedback` and retried, and a second
        raises `LLMError("LLM_OUTPUT_INVALID")`. Reimplementing that here would duplicate
        it and drift from the rest of the agents layer.
        """
        boundary = ReasoningBoundary(
            provider=self._llm, settings=self._settings, clock=self._now_fn
        )
        request = SemanticRouteInput(
            event=_summarize_event(event), candidates=_prompt_candidates(summaries)
        )
        try:
            result = await boundary.run(
                task="route",
                prompt=Prompt(ROUTER_VERSION, router_prompt()),
                request=request,
                output_type=RouteDraft,
                convert=lambda draft, _context: map_route_draft(draft),
                # map_route_draft already raises ValueError on a contradictory draft, and
                # the boundary treats that as the validation failure worth repairing.
                validate=lambda result: result,
            )
        except LLMError as exc:
            logger.warning(
                "Event router semantic stage failed: %s (attempts: %s)",
                exc.code,
                [attempt.outcome for attempt in exc.attempts],
            )
            return None
        return result.value

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
        *,
        new_loop_candidate: bool = False,
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
            # Enforced here rather than trusted from the model, and again in validation.
            create_new_loop_candidate=new_loop_candidate and not ordered,
        )
        return validate_route_response(response, by_id.keys())
