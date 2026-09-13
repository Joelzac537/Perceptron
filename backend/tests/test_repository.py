"""Tests for the Event Router's read layer and its loop summaries."""

import pytest
from fixtures.router import load_loop_rows

from app.constants import LoopStatus
from app.events.repository import (
    FixtureLoopRepository,
    LoopRepository,
    UnavailableLLM,
)
from app.events.router_models import (
    IDENTITY_FIELDS,
    OPEN_NODE_STATUSES,
    ROUTABLE_LOOP_STATUSES,
    TOKEN_IDENTITY_FIELDS,
    build_loop_summary,
)

OWNING_USER = "user_001"
OTHER_USER = "user_999"

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo() -> FixtureLoopRepository:
    return FixtureLoopRepository(load_loop_rows())


# --------------------------------------------------------------------------------------
# Tenant and status scoping
# --------------------------------------------------------------------------------------


async def test_owning_user_gets_exactly_four_routable_loops(repo: FixtureLoopRepository) -> None:
    summaries = await repo.list_routable_loops(OWNING_USER)
    assert [s.loop_id for s in summaries] == [
        "loop_refund_001",
        "loop_presentation_001",
        "loop_insurance_001",
        "loop_insurance_002",
    ]


async def test_completed_loop_is_never_a_candidate(repo: FixtureLoopRepository) -> None:
    """A late event must not reopen a finished loop."""
    ids = {s.loop_id for s in await repo.list_routable_loops(OWNING_USER)}
    assert "loop_old_refund_777" not in ids


async def test_other_users_loop_is_never_a_candidate(repo: FixtureLoopRepository) -> None:
    """loop_other_user_500 shares an order id with loop_refund_001; only the tenant filter
    separates them."""
    ids = {s.loop_id for s in await repo.list_routable_loops(OWNING_USER)}
    assert "loop_other_user_500" not in ids


async def test_other_user_gets_exactly_one(repo: FixtureLoopRepository) -> None:
    summaries = await repo.list_routable_loops(OTHER_USER)
    assert [s.loop_id for s in summaries] == ["loop_other_user_500"]


async def test_every_returned_status_is_routable(repo: FixtureLoopRepository) -> None:
    for summary in await repo.list_routable_loops(OWNING_USER):
        assert summary.status in ROUTABLE_LOOP_STATUSES


async def test_unknown_user_gets_nothing(repo: FixtureLoopRepository) -> None:
    assert await repo.list_routable_loops("user_does_not_exist") == []


# --------------------------------------------------------------------------------------
# Thread lookup
# --------------------------------------------------------------------------------------


async def test_thread_collision_is_split_by_tenant(repo: FixtureLoopRepository) -> None:
    """thread_gmail_444 is listed on two loops owned by different users."""
    found = await repo.find_loop_ids_by_thread(OWNING_USER, ["thread_gmail_444"])
    assert found == {"thread_gmail_444": ["loop_refund_001"]}


async def test_thread_collision_resolves_to_the_other_loop_for_the_other_user(
    repo: FixtureLoopRepository,
) -> None:
    found = await repo.find_loop_ids_by_thread(OTHER_USER, ["thread_gmail_444"])
    assert found == {"thread_gmail_444": ["loop_other_user_500"]}


async def test_thread_lookup_returns_lists_not_bare_ids(repo: FixtureLoopRepository) -> None:
    """The list shape is load-bearing: a thread may map to several loops."""
    found = await repo.find_loop_ids_by_thread(OWNING_USER, ["thread_gmail_444"])
    assert isinstance(found["thread_gmail_444"], list)


async def test_thread_on_a_completed_loop_is_not_found(repo: FixtureLoopRepository) -> None:
    """thread_gmail_119 belongs to loop_old_refund_777, which is COMPLETED."""
    assert await repo.find_loop_ids_by_thread(OWNING_USER, ["thread_gmail_119"]) == {}


async def test_unknown_thread_is_omitted(repo: FixtureLoopRepository) -> None:
    assert await repo.find_loop_ids_by_thread(OWNING_USER, ["thread_nope"]) == {}


async def test_multiple_threads_queried_at_once(repo: FixtureLoopRepository) -> None:
    found = await repo.find_loop_ids_by_thread(
        OWNING_USER, ["thread_gmail_444", "thread_gmail_612", "thread_nope"]
    )
    assert found == {
        "thread_gmail_444": ["loop_refund_001"],
        "thread_gmail_612": ["loop_insurance_001"],
    }


# --------------------------------------------------------------------------------------
# LoopSummary contents
# --------------------------------------------------------------------------------------


async def _summary(repo: FixtureLoopRepository, loop_id: str):
    summaries = await repo.list_routable_loops(OWNING_USER)
    return next(s for s in summaries if s.loop_id == loop_id)


async def test_presentation_loop_people_and_identifiers(repo: FixtureLoopRepository) -> None:
    summary = await _summary(repo, "loop_presentation_001")
    assert summary.people == ["Sarah"]
    assert summary.identifiers["version"] == "final"


async def test_version_is_harvested_but_not_token_matchable(repo: FixtureLoopRepository) -> None:
    """`version` reaches the prompt yet stays out of literal matching, since "final" is an
    ordinary English word that would match unrelated prose."""
    summary = await _summary(repo, "loop_presentation_001")
    assert "version" in summary.identifiers
    assert "version" not in TOKEN_IDENTITY_FIELDS
    assert "version" in IDENTITY_FIELDS


async def test_requirement_wins_over_node_metadata_on_conflict(
    repo: FixtureLoopRepository,
) -> None:
    """node_presentation_collect says version "draft"; its requirement says "final"."""
    rows = {r["loop"]["id"]: r for r in load_loop_rows()}
    node_metadata = rows["loop_presentation_001"]["nodes"][0]["metadata"]
    requirement_fields = rows["loop_presentation_001"]["requirements"][0]["required_fields"]
    assert node_metadata["version"] == "draft"
    assert requirement_fields["version"] == "final"

    summary = await _summary(repo, "loop_presentation_001")
    assert summary.identifiers["version"] == "final"


async def test_non_identity_metadata_is_dropped(repo: FixtureLoopRepository) -> None:
    """"artifact" and "channel" are on the node but are not identity fields."""
    summary = await _summary(repo, "loop_presentation_001")
    assert set(summary.identifiers) <= IDENTITY_FIELDS
    assert "artifact" not in summary.identifiers
    assert "channel" not in summary.identifiers


async def test_refund_loop_harvests_only_open_nodes(repo: FixtureLoopRepository) -> None:
    """node_refund_requested is VERIFIED, so its owner "Alex" must not appear."""
    summary = await _summary(repo, "loop_refund_001")
    assert summary.people == []
    assert summary.open_node_titles == ["Merchant confirms the refund was issued"]
    assert summary.identifiers["order_id"] == "A1298"
    assert summary.identifiers["amount"] == 129.0


async def test_settled_node_statuses_are_excluded() -> None:
    from app.constants import NodeStatus

    for settled in (NodeStatus.VERIFIED, NodeStatus.FAILED, NodeStatus.CANCELLED,
                    NodeStatus.SUPERSEDED):
        assert settled not in OPEN_NODE_STATUSES


async def test_terminal_loop_statuses_are_excluded() -> None:
    for terminal in (LoopStatus.COMPLETED, LoopStatus.FAILED, LoopStatus.CANCELLED):
        assert terminal not in ROUTABLE_LOOP_STATUSES


# --------------------------------------------------------------------------------------
# prompt_view
# --------------------------------------------------------------------------------------


async def test_prompt_view_omits_thread_ids(repo: FixtureLoopRepository) -> None:
    summary = await _summary(repo, "loop_refund_001")
    assert summary.thread_ids == ["thread_gmail_444"]

    view = summary.prompt_view()
    assert set(view) == {"loop_id", "goal", "open_nodes", "people", "identifiers"}
    assert "thread_gmail_444" not in str(view)


async def test_prompt_view_returns_copies(repo: FixtureLoopRepository) -> None:
    summary = await _summary(repo, "loop_refund_001")
    view = summary.prompt_view()
    view["identifiers"]["order_id"] = "TAMPERED"
    assert summary.identifiers["order_id"] == "A1298"


# --------------------------------------------------------------------------------------
# build_loop_summary edge cases
# --------------------------------------------------------------------------------------


async def test_loop_with_no_open_nodes_summarises_empty() -> None:
    summary = build_loop_summary(
        loop_row={
            "id": "loop_x",
            "user_id": OWNING_USER,
            "title": "t",
            "goal": "g",
            "status": "ACTIVE",
            "updated_at": "2026-09-12T16:20:00-04:00",
        },
        node_rows=[
            {"id": "n1", "title": "done", "status": "VERIFIED", "owner": "Alex",
             "metadata": {"order_id": "GONE"}}
        ],
        requirement_rows=[
            {"id": "r1", "node_id": "n1", "type": "T", "description": "d",
             "source_apps": ["gmail"], "required_fields": {"order_id": "ALSO_GONE"}}
        ],
        thread_ids=[],
    )
    assert summary.open_node_titles == []
    assert summary.people == []
    assert summary.identifiers == {}


async def test_requirement_on_a_settled_node_is_ignored() -> None:
    summary = build_loop_summary(
        loop_row={
            "id": "loop_y",
            "user_id": OWNING_USER,
            "title": "t",
            "goal": "g",
            "status": "WAITING",
            "updated_at": "2026-09-12T16:20:00-04:00",
        },
        node_rows=[
            {"id": "open", "title": "open", "status": "WAITING", "owner": None,
             "metadata": {"order_id": "KEEP"}},
            {"id": "shut", "title": "shut", "status": "CANCELLED", "owner": None,
             "metadata": {}},
        ],
        requirement_rows=[
            {"id": "r1", "node_id": "shut", "type": "T", "description": "d",
             "source_apps": ["gmail"], "required_fields": {"order_id": "OVERWRITE"}}
        ],
        thread_ids=[],
    )
    assert summary.identifiers["order_id"] == "KEEP"


# --------------------------------------------------------------------------------------
# Protocol conformance and the LLM guard
# --------------------------------------------------------------------------------------


async def test_fixture_repository_satisfies_the_protocol(repo: FixtureLoopRepository) -> None:
    assert isinstance(repo, LoopRepository)


async def test_unavailable_llm_raises_on_call() -> None:
    llm = UnavailableLLM()
    with pytest.raises(AssertionError, match="must stay deterministic"):
        llm.complete("any prompt")


async def test_deterministic_reads_never_touch_the_model(repo: FixtureLoopRepository) -> None:
    """The repository layer is regex-and-SQL only; holding an UnavailableLLM is safe."""
    _llm = UnavailableLLM()
    assert await repo.list_routable_loops(OWNING_USER)
    assert await repo.find_loop_ids_by_thread(OWNING_USER, ["thread_gmail_444"])
