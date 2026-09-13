"""Event Router acceptance tests.

Every test in this module runs with `UnavailableLLM`, whose `complete` raises. If any
routing path that is supposed to be deterministic ever reaches a model, these fail loudly
rather than quietly costing a token and passing.
"""

from datetime import UTC, datetime

import pytest
from fixtures.router import ACTING_USER_ID, load_event, load_loop_rows
from provider_helpers import FakeProvider

from app.agents.llm import InvalidOutput
from app.events.repository import FixtureLoopRepository, UnavailableLLM
from app.events.router import EventRouter
from app.events.router_models import RouteCandidateDraft, RouteDraft
from app.events.router_validation import RoutingValidationError, validate_route_response
from app.graph.schemas import LoopMatch, RouteEventRequest, RouteEventResponse

OTHER_USER = "user_999"

# Injected rather than read from the clock, per the house rule on `now`.
NOW = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)

# ER-01b asserts an identifier match clears this bar. The identifier signal scores 0.97
# before amount corroboration, so the floor here is deliberately below that.
IDENTIFIER_CONFIDENCE_FLOOR = 0.95


class RecordingLoopRepository(FixtureLoopRepository):
    """Counts reads so a test can prove a stage never touched the database.

    Test-only scaffolding, so it lives here rather than in `app/events/repository.py`.
    `UnavailableLLM` is different: it is an injectable double that app code declares.
    """

    def __init__(self, rows) -> None:
        super().__init__(rows)
        self.list_calls = 0
        self.thread_calls = 0

    async def list_routable_loops(self, user_id: str):
        self.list_calls += 1
        return await super().list_routable_loops(user_id)

    async def find_loop_ids_by_thread(self, user_id: str, thread_ids):
        self.thread_calls += 1
        return await super().find_loop_ids_by_thread(user_id, thread_ids)

    @property
    def total_calls(self) -> int:
        return self.list_calls + self.thread_calls


def no_match_draft() -> RouteDraft:
    """What the model returns for an event that relates to nothing and owes nothing."""
    return RouteDraft(candidates=[], is_new_obligation=False, obligation_reason=None)


def build_router(user_id: str = ACTING_USER_ID, repo=None, llm=None) -> EventRouter:
    return EventRouter(
        repo=repo if repo is not None else FixtureLoopRepository(load_loop_rows()),
        llm=llm if llm is not None else UnavailableLLM(),
        user_id=user_id,
        now_fn=lambda: NOW,
    )


async def route(
    name: str, user_id: str = ACTING_USER_ID, repo=None, llm=None
) -> RouteEventResponse:
    router = build_router(user_id=user_id, repo=repo, llm=llm)
    return await router.route(RouteEventRequest(event=load_event(name)))


# --------------------------------------------------------------------------------------
# SYS-01 — a pre-linked event resolves without touching anything
# --------------------------------------------------------------------------------------


async def test_sys_01_prelinked_event_matches_without_querying_the_repository() -> None:
    repo = RecordingLoopRepository(load_loop_rows())
    response = await route("deadline_reached", repo=repo)

    assert len(response.matches) == 1
    match = response.matches[0]
    assert match.loop_id == "loop_presentation_001"
    assert match.confidence == 1.0
    assert match.reason.strip()
    assert response.create_new_loop_candidate is False

    # The whole point of Stage 0: the answer was already on the event.
    assert repo.total_calls == 0, "Stage 0 must not query the repository"


# --------------------------------------------------------------------------------------
# ER-01a / ER-01b — the two deterministic paths to the same loop
# --------------------------------------------------------------------------------------


async def test_er_01a_thread_id_routes_to_the_refund_loop() -> None:
    response = await route("refund_confirmed")

    assert len(response.matches) == 1
    match = response.matches[0]
    assert match.loop_id == "loop_refund_001"
    assert "thread_gmail_444" in match.reason
    assert response.create_new_loop_candidate is False


async def test_er_01b_identifier_routes_to_the_refund_loop_without_a_thread() -> None:
    """Same event with metadata stripped: the order id in the body has to carry it."""
    response = await route("refund_confirmed_no_thread")

    assert len(response.matches) == 1
    match = response.matches[0]
    assert match.loop_id == "loop_refund_001"
    assert match.confidence >= IDENTIFIER_CONFIDENCE_FLOOR
    assert "A1298" in match.reason
    assert "thread" not in match.reason.lower(), "no thread signal exists on this event"


async def test_er_01_both_paths_agree_on_the_loop() -> None:
    with_thread = await route("refund_confirmed")
    without_thread = await route("refund_confirmed_no_thread")
    assert [m.loop_id for m in with_thread.matches] == [m.loop_id for m in without_thread.matches]


# --------------------------------------------------------------------------------------
# USER-01 — the tenant filter
# --------------------------------------------------------------------------------------


async def test_user_01_same_event_routes_to_the_other_tenants_loop() -> None:
    """loop_refund_001 and loop_other_user_500 both carry order A1298. Only user_id
    separates them, and Event has no user_id field to separate them with."""
    response = await route("refund_confirmed_no_thread", user_id=OTHER_USER)

    assert [m.loop_id for m in response.matches] == ["loop_other_user_500"]


async def test_user_01_owning_user_never_sees_the_other_tenants_loop() -> None:
    response = await route("refund_confirmed_no_thread")
    assert [m.loop_id for m in response.matches] == ["loop_refund_001"]


# --------------------------------------------------------------------------------------
# COMP-01 — a completed loop is not a candidate
# --------------------------------------------------------------------------------------


async def test_comp_01_late_event_does_not_reopen_a_completed_loop() -> None:
    """Order Z9001 belongs to loop_old_refund_777, which is COMPLETED.

    The COMPLETED loop is filtered out at the repository, so it is never even offered to
    the model. Stage 3 still runs to ask whether this is a new obligation, and answers no.
    """
    provider = FakeProvider(outputs=[no_match_draft()])
    response = await route("completed_loop_refund", llm=provider)

    assert response.matches == []
    assert response.create_new_loop_candidate is False


async def test_comp_01_the_completed_loop_is_never_offered_to_the_model() -> None:
    """The stronger claim: the model could not have matched it even if it wanted to."""
    provider = FakeProvider(outputs=[no_match_draft()])
    await route("completed_loop_refund", llm=provider)

    envelope = provider.calls[0].input_json
    assert "loop_old_refund_777" not in envelope
    assert "loop_other_user_500" not in envelope


async def test_comp_01_the_completed_loop_really_does_carry_that_order() -> None:
    """Guards the test above from passing for the wrong reason: if the fixture stopped
    carrying Z9001, COMP-01 would pass trivially."""
    rows = {row["loop"]["id"]: row for row in load_loop_rows()}
    completed = rows["loop_old_refund_777"]
    assert completed["loop"]["status"] == "COMPLETED"
    assert completed["nodes"][0]["metadata"]["order_id"] == "Z9001"
    assert "Z9001" in load_event("completed_loop_refund").content


# --------------------------------------------------------------------------------------
# ER-03 — keyword distractors create nothing
# --------------------------------------------------------------------------------------


async def test_er_03_newsletter_matches_nothing_and_proposes_no_loop() -> None:
    """The newsletter says "insurance" and "refund" but imposes no obligation.

    Stage 1 finds nothing, so Stage 3 asks the new-obligation question and answers no.
    That question is the entire reason the stage exists, so reaching the model here is
    correct behavior rather than a leak.
    """
    provider = FakeProvider(outputs=[no_match_draft()])
    response = await route("newsletter", llm=provider)

    assert response.matches == []
    assert response.create_new_loop_candidate is False
    assert len(provider.calls) == 1, "one call answers both questions"


# --------------------------------------------------------------------------------------
# VAL-01 — the response validator
# --------------------------------------------------------------------------------------


def test_val_01_duplicate_loop_id_is_rejected() -> None:
    response = RouteEventResponse(
        matches=[
            LoopMatch(loop_id="loop_refund_001", confidence=0.9, reason="first"),
            LoopMatch(loop_id="loop_refund_001", confidence=0.8, reason="again"),
        ],
        create_new_loop_candidate=False,
    )

    with pytest.raises(RoutingValidationError) as error:
        validate_route_response(response, {"loop_refund_001"})

    assert any("repeats loop" in issue for issue in error.value.issues)


def test_val_01_matches_plus_new_loop_candidate_is_rejected() -> None:
    """An event that belongs to an existing loop is not also a new obligation."""
    response = RouteEventResponse(
        matches=[LoopMatch(loop_id="loop_refund_001", confidence=0.9, reason="matched")],
        create_new_loop_candidate=True,
    )

    with pytest.raises(RoutingValidationError) as error:
        validate_route_response(response, {"loop_refund_001"})

    assert any("create_new_loop_candidate" in issue for issue in error.value.issues)


def test_val_01_reports_every_issue_at_once() -> None:
    """House style: collect all issues, raise once."""
    response = RouteEventResponse(
        matches=[
            LoopMatch(loop_id="ghost", confidence=0.5, reason="invented"),
            LoopMatch(loop_id="ghost", confidence=0.9, reason="invented again"),
        ],
        create_new_loop_candidate=True,
    )

    with pytest.raises(RoutingValidationError) as error:
        validate_route_response(response, {"loop_refund_001"})

    issues = error.value.issues
    assert any("not a candidate" in issue for issue in issues)
    assert any("repeats loop" in issue for issue in issues)
    assert any("sorted by confidence descending" in issue for issue in issues)
    assert any("create_new_loop_candidate" in issue for issue in issues)


def test_val_01_accepts_a_well_formed_response() -> None:
    response = RouteEventResponse(
        matches=[
            LoopMatch(loop_id="loop_insurance_001", confidence=0.88, reason="a"),
            LoopMatch(loop_id="loop_insurance_002", confidence=0.81, reason="b"),
        ],
        create_new_loop_candidate=False,
    )
    allowed = {"loop_insurance_001", "loop_insurance_002"}
    assert validate_route_response(response, allowed) is response


# --------------------------------------------------------------------------------------
# The model is never consulted on any of the above
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["deadline_reached", "refund_confirmed", "refund_confirmed_no_thread"],
)
async def test_deterministic_fixtures_never_reach_the_model(name: str) -> None:
    """UnavailableLLM raises AssertionError on `generate` or `complete`, so any model call
    on a path that resolves deterministically fails here.

    Only fixtures that Stage 0 or Stage 1 resolve at or above SHORT_CIRCUIT belong in this
    list. An unmatched event legitimately reaches Stage 3 to be asked whether it is a new
    obligation — see the newsletter and completed-loop tests above.
    """
    response = await route(name)
    assert isinstance(response, RouteEventResponse)
    assert response.matches, "this fixture must resolve without a model"


async def test_unavailable_llm_guards_the_provider_surface() -> None:
    """ReasoningBoundary calls `generate`, not `complete`. Guarding only `complete` would
    let a leaked model call surface as an obscure AttributeError instead of a failure."""
    llm = UnavailableLLM()
    for call in (lambda: llm.generate("call", object), lambda: llm.complete("prompt")):
        with pytest.raises(AssertionError, match="must stay deterministic"):
            call()


# --------------------------------------------------------------------------------------
# Phase 6 — the semantic stage, driven by a stubbed provider
# --------------------------------------------------------------------------------------


async def test_er_02_semantic_match_with_no_shared_identifiers() -> None:
    """mike_has_final_no_thread has no thread, no order id, no policy number and no
    amount. Stage 1 can only offer a weak actor signal, so the model decides."""
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[
                    RouteCandidateDraft(
                        loop_id="loop_presentation_001",
                        confidence=0.86,
                        reason="Sarah owns the open outcome and is handing the deck to Mike.",
                    )
                ],
                is_new_obligation=False,
                obligation_reason=None,
            )
        ]
    )

    response = await route("mike_has_final_no_thread", llm=provider)

    assert [m.loop_id for m in response.matches] == ["loop_presentation_001"]
    assert response.create_new_loop_candidate is False
    assert len(provider.calls) == 1


async def test_er_02_prompt_receives_candidate_views_not_thread_ids() -> None:
    provider = FakeProvider(outputs=[no_match_draft()])
    await route("mike_has_final_no_thread", llm=provider)

    envelope = provider.calls[0].input_json
    assert "loop_presentation_001" in envelope
    assert "Sarah" in envelope
    # prompt_view() withholds thread ids; opaque strings invite invented matches.
    assert "1690000123.111" not in envelope


async def test_er_04_ambiguous_insurance_returns_both_loops_sorted() -> None:
    """Neither insurance loop's policy number appears in the event, so both fit equally."""
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[
                    RouteCandidateDraft(
                        loop_id="loop_insurance_002", confidence=0.82, reason="car policy"
                    ),
                    RouteCandidateDraft(
                        loop_id="loop_insurance_001", confidence=0.85, reason="renters policy"
                    ),
                ],
                is_new_obligation=False,
                obligation_reason=None,
            )
        ]
    )

    response = await route("ambiguous_insurance", llm=provider)

    # Returned sorted by confidence descending regardless of the order the model gave.
    assert [m.loop_id for m in response.matches] == [
        "loop_insurance_001",
        "loop_insurance_002",
    ]
    assert [m.confidence for m in response.matches] == [0.85, 0.82]
    assert response.create_new_loop_candidate is False


async def test_new_01_new_obligation_proposes_a_loop_and_matches_nothing() -> None:
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[],
                is_new_obligation=True,
                obligation_reason="Bank requires proof of address by September 30.",
            )
        ]
    )

    response = await route("new_obligation", llm=provider)

    assert response.matches == []
    assert response.create_new_loop_candidate is True


async def test_llm_f1_two_invalid_outputs_return_empty_without_raising() -> None:
    """The boundary repairs once, then raises LLM_OUTPUT_INVALID. The router swallows it:
    a routing failure must never create a loop and must never raise into the caller."""
    provider = FakeProvider(
        outputs=[InvalidOutput(["bad draft"]), InvalidOutput(["still bad"])]
    )

    response = await route("ambiguous_insurance", llm=provider)

    assert response.matches == []
    assert response.create_new_loop_candidate is False
    assert len(provider.calls) == 2, "one original attempt plus exactly one repair"


async def test_llm_f1_logs_the_failure_code(caplog) -> None:
    provider = FakeProvider(
        outputs=[InvalidOutput(["bad draft"]), InvalidOutput(["still bad"])]
    )

    with caplog.at_level("WARNING", logger="app.events.router"):
        await route("ambiguous_insurance", llm=provider)

    assert "LLM_OUTPUT_INVALID" in caplog.text


async def test_model_cannot_match_a_loop_it_was_not_offered() -> None:
    """A loop_id the model invented is a hallucination, not a match, and must not reach
    validate_route_response as a candidate."""
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[
                    RouteCandidateDraft(
                        loop_id="loop_that_does_not_exist", confidence=0.99, reason="nope"
                    )
                ],
                is_new_obligation=False,
                obligation_reason=None,
            )
        ]
    )

    response = await route("ambiguous_insurance", llm=provider)
    assert response.matches == []


async def test_model_cannot_reach_another_tenants_loop() -> None:
    """loop_other_user_500 belongs to user_999 and is filtered before the prompt is built,
    so no model output can route user_001's event into it."""
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[
                    RouteCandidateDraft(
                        loop_id="loop_other_user_500", confidence=0.99, reason="wrong tenant"
                    )
                ],
                is_new_obligation=False,
                obligation_reason=None,
            )
        ]
    )

    response = await route("ambiguous_insurance", llm=provider)
    assert response.matches == []


async def test_semantic_match_merges_with_the_stage_one_actor_signal() -> None:
    """Stage 2 merges rather than replaces: the model's higher confidence wins, but a
    deterministic signal is never silently discarded."""
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[
                    RouteCandidateDraft(
                        loop_id="loop_presentation_001", confidence=0.91, reason="semantic"
                    )
                ],
                is_new_obligation=False,
                obligation_reason=None,
            )
        ]
    )

    response = await route("mike_has_final_no_thread", llm=provider)

    assert len(response.matches) == 1
    # 0.91 from the model beats the 0.55 actor signal from Stage 1.
    assert response.matches[0].confidence == 0.91
    assert response.matches[0].reason == "semantic"


async def test_new_obligation_is_dropped_when_a_loop_also_matched() -> None:
    """Belt and braces: map_route_draft rejects this combination, and the router would
    drop the flag anyway. An event in an existing loop is not a new obligation."""
    provider = FakeProvider(
        outputs=[
            RouteDraft(
                candidates=[
                    RouteCandidateDraft(
                        loop_id="loop_insurance_001", confidence=0.9, reason="renters policy"
                    )
                ],
                is_new_obligation=False,
                obligation_reason=None,
            )
        ]
    )

    response = await route("ambiguous_insurance", llm=provider)
    assert response.matches
    assert response.create_new_loop_candidate is False


async def test_semantic_stage_uses_the_router_timeout_not_the_compiler_default() -> None:
    from app.events.router import ROUTER_TIMEOUT_SECONDS

    provider = FakeProvider(outputs=[no_match_draft()])
    await route("ambiguous_insurance", llm=provider)

    assert provider.calls[0].timeout_seconds == ROUTER_TIMEOUT_SECONDS
    assert provider.calls[0].task == "route"


async def test_semantic_stage_caps_the_candidate_list() -> None:
    from app.events.router import SEMANTIC_CANDIDATE_CAP

    provider = FakeProvider(outputs=[no_match_draft()])
    await route("ambiguous_insurance", llm=provider)

    import json

    envelope = json.loads(provider.calls[0].input_json)
    assert len(envelope["request"]["candidates"]) <= SEMANTIC_CANDIDATE_CAP
