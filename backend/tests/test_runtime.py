"""The write side: does an EventPlan become the right rows?

The pipeline's own tests prove what it decides. These prove what the runtime does
about it, so the database is faked at the `app.db` boundary — the point of the split
is that neither half needs Postgres to be tested.
"""

from datetime import UTC, datetime

import pytest

from app.events.pipeline import EventPlan, LoopOutcome
from app.graph.schemas import (
    Event,
    EventType,
    LoopMatch,
    NodeEvidenceDecision,
    VerifyEventResponse,
)
from app.runtime import runtime as runtime_module
from app.runtime.runtime import LoopRuntime, _plain

USER = "user_1"
APPS = ["gmail", "slack"]


def make_event(event_id: str = "event_1", **overrides) -> Event:
    fields = {
        "id": event_id,
        "source_app": "gmail",
        "event_type": EventType.MESSAGE_RECEIVED,
        "external_id": "gmail_1",
        "timestamp": datetime(2026, 9, 13, 12, 0, tzinfo=UTC),
        "content": "Your refund has been processed.",
    }
    fields.update(overrides)
    return Event(**fields)


class FakeDB:
    """Stands in for `app.db.db`, recording what the runtime asked it to write."""

    def __init__(self, *, created: bool = True) -> None:
        self.created = created
        self.inserted: list[dict] = []
        self.processed: list[str] = []
        self.graphs: list[dict] = []
        self.operations: list[tuple[str, list[dict]]] = []
        self.actions: list[dict] = []

    async def insert_event(self, event: dict) -> tuple[dict, bool]:
        self.inserted.append(event)
        return {**event, "id": event.get("id") or "event_generated"}, self.created

    async def mark_event_processed(self, event_id: str) -> None:
        self.processed.append(event_id)

    async def persist_compiled_graph(self, **kwargs) -> str:
        self.graphs.append(kwargs)
        return "loop_new"

    async def apply_graph_operations(self, loop_id: str, operations: list[dict]) -> None:
        self.operations.append((loop_id, operations))

    async def create_action(self, action: dict) -> tuple[dict, bool]:
        self.actions.append(action)
        return action, True


class FakeQueries:
    def __init__(self, graph: dict | None = None) -> None:
        self.graph = graph
        self.evidence: list[dict] = []
        self.links: list[tuple[str, str]] = []
        self.source_events: list[tuple[str, str]] = []

    async def insert_evidence(self, **kwargs) -> str:
        self.evidence.append(kwargs)
        return f"evidence_{len(self.evidence)}"

    async def link_event_to_loop(self, event_id: str, loop_id: str) -> None:
        self.links.append((event_id, loop_id))

    async def record_source_event(self, loop_id: str, event_id: str) -> None:
        self.source_events.append((loop_id, event_id))

    async def load_loop_graph(self, user_id: str, loop_id: str):
        return self.graph

    async def load_replan_context_rows(self, loop_id: str) -> dict:
        return {"requirements": [], "evidence": [], "actions": []}

    async def applied_event_ids(self, loop_id: str) -> list[str]:
        return []


class StubPipeline:
    def __init__(self, plan: EventPlan) -> None:
        self.plan = plan
        self.seen: list[Event] = []

    async def process(self, event: Event) -> EventPlan:
        self.seen.append(event)
        return self.plan


@pytest.fixture
def fakes(monkeypatch):
    fake_db, fake_queries = FakeDB(), FakeQueries()
    monkeypatch.setattr(runtime_module, "db", fake_db)
    monkeypatch.setattr(runtime_module, "queries", fake_queries)
    return fake_db, fake_queries


def build(pipeline, **kwargs) -> LoopRuntime:
    return LoopRuntime(pipeline, user_id=USER, available_apps=APPS, **kwargs)


# --- dedup -----------------------------------------------------------------


async def test_duplicate_event_stops_before_planning(monkeypatch):
    fake_db = FakeDB(created=False)
    monkeypatch.setattr(runtime_module, "db", fake_db)
    monkeypatch.setattr(runtime_module, "queries", FakeQueries())

    pipeline = StubPipeline(EventPlan(event_id="event_1"))
    result = await build(pipeline).handle(make_event())

    assert result.duplicate is True
    assert pipeline.seen == [], "a duplicate must not be routed a second time"
    assert fake_db.processed == []


async def test_routing_failure_leaves_the_event_unprocessed(fakes):
    """A throttled model must not kill the poller, and must not fake success either."""
    fake_db, _ = fakes

    class Exploding:
        async def process(self, event):
            raise RuntimeError("429 rate limited")

    result = await build(Exploding()).handle(make_event())

    assert result.stored is True
    assert any("routing failed" in error for error in result.errors)
    assert fake_db.processed == [], "an unrouted event is not processed"


async def test_new_event_is_marked_processed(fakes):
    fake_db, _ = fakes
    result = await build(StubPipeline(EventPlan(event_id="event_1"))).handle(make_event())

    assert result.stored is True
    assert fake_db.processed == ["event_1"]


# --- compiling a new loop --------------------------------------------------


class StubCompiler:
    def __init__(self, graph) -> None:
        self.graph = graph
        self.requests: list = []

    async def compile(self, request):
        self.requests.append(request)
        return self.graph


async def test_unmatched_event_compiles_and_links(fakes, refund_data):
    fake_db, fake_queries = fakes
    from app.graph.schemas import CompiledGraph

    graph = CompiledGraph.model_validate(refund_data)
    compiler = StubCompiler(graph)

    plan = EventPlan(event_id="event_1", compile_new_loop=True)
    result = await build(StubPipeline(plan), compiler=compiler).handle(make_event())

    assert result.compiled_loop_id == "loop_new"
    assert len(fake_db.graphs) == 1
    assert fake_db.graphs[0]["source_event_ids"] == ["event_1"]
    assert fake_queries.links == [("event_1", "loop_new")]
    assert compiler.requests[0].user_id == USER
    assert compiler.requests[0].available_apps == APPS


async def test_compile_without_compiler_is_reported_not_raised(fakes):
    plan = EventPlan(event_id="event_1", compile_new_loop=True)
    result = await build(StubPipeline(plan)).handle(make_event())

    assert result.compiled_loop_id is None
    assert any("no compiler" in error for error in result.errors)


async def test_compiler_failure_does_not_propagate(fakes):
    class Exploding:
        async def compile(self, request):
            raise RuntimeError("model refused")

    plan = EventPlan(event_id="event_1", compile_new_loop=True)
    result = await build(StubPipeline(plan), compiler=Exploding()).handle(make_event())

    assert result.compiled_loop_id is None
    assert any("compile failed" in error for error in result.errors)


# --- evidence on a matched loop --------------------------------------------


def outcome_with(*decisions, requires_replan: bool = False) -> LoopOutcome:
    return LoopOutcome(
        loop_id="loop_1",
        match=LoopMatch(loop_id="loop_1", confidence=0.9, reason="same order id"),
        verification=VerifyEventResponse(
            decisions=list(decisions), requires_replan=requires_replan
        ),
    )


def decision(node_id: str, *, satisfies: bool) -> NodeEvidenceDecision:
    return NodeEvidenceDecision(
        node_id=node_id,
        relationship="PROVES" if satisfies else "PARTIALLY_SUPPORTS",
        confidence=0.95,
        reason="Merchant confirmed the refund",
        evidence_satisfies_requirement=satisfies,
    )


async def test_satisfying_evidence_verifies_the_node(fakes):
    fake_db, fake_queries = fakes
    proven = outcome_with(decision("node_1", satisfies=True))
    plan = EventPlan(event_id="event_1", outcomes=(proven,))

    result = await build(StubPipeline(plan)).handle(make_event())

    assert result.evidence_written == 1
    assert fake_queries.evidence[0]["verified"] is True
    assert fake_queries.evidence[0]["node_id"] == "node_1"

    loop_id, operations = fake_db.operations[0]
    assert loop_id == "loop_1"
    assert operations[0]["type"] == "VERIFY_NODE"
    assert operations[0]["target_id"] == "node_1"
    assert operations[0]["reason"], "a status change must carry a reason for the activity log"


async def test_non_satisfying_evidence_is_recorded_without_verifying(fakes):
    fake_db, fake_queries = fakes
    plan = EventPlan(
        event_id="event_1", outcomes=(outcome_with(decision("node_1", satisfies=False)),)
    )

    result = await build(StubPipeline(plan)).handle(make_event())

    assert result.evidence_written == 1
    assert result.nodes_verified == 0
    assert fake_db.operations == [], "nothing was proven, so no node changes status"


async def test_single_match_sets_the_routing_hint(fakes):
    _, fake_queries = fakes
    proven = outcome_with(decision("node_1", satisfies=True))
    plan = EventPlan(event_id="event_1", outcomes=(proven,))

    await build(StubPipeline(plan)).handle(make_event())

    assert fake_queries.links == [("event_1", "loop_1")]
    assert ("loop_1", "event_1") in fake_queries.source_events


async def test_ambiguous_match_records_provenance_but_no_hint(fakes):
    """A thread shared by two loops leaves linked_loop_id null on purpose."""
    _, fake_queries = fakes
    second = LoopOutcome(
        loop_id="loop_2",
        match=LoopMatch(loop_id="loop_2", confidence=0.8, reason="same thread"),
        verification=VerifyEventResponse(decisions=[]),
    )
    plan = EventPlan(
        event_id="event_1",
        outcomes=(outcome_with(decision("node_1", satisfies=True)), second),
    )

    await build(StubPipeline(plan)).handle(make_event())

    assert fake_queries.links == [], "the column holds one id, so ambiguity writes none"
    assert sorted(fake_queries.source_events) == [("loop_1", "event_1"), ("loop_2", "event_1")]


async def test_unverifiable_loop_keeps_provenance_and_reports(fakes):
    _, fake_queries = fakes
    outcome = LoopOutcome(
        loop_id="loop_1",
        match=LoopMatch(loop_id="loop_1", confidence=0.9, reason="same order id"),
        verification=None,
        error="loop_1: not loadable for this user",
    )
    result = await build(StubPipeline(EventPlan(event_id="event_1", outcomes=(outcome,)))).handle(
        make_event()
    )

    assert result.evidence_written == 0
    assert ("loop_1", "event_1") in fake_queries.source_events
    assert any("not loadable" in error for error in result.errors)


# --- helpers ---------------------------------------------------------------


def test_plain_unwraps_enums_recursively():
    payload = {
        "event_type": EventType.DOCUMENT_DELETED,
        "nested": [{"type": EventType.MESSAGE_SENT}],
        "when": datetime(2026, 9, 13, tzinfo=UTC),
    }
    result = _plain(payload)

    assert result["event_type"] == "DOCUMENT_DELETED"
    assert type(result["event_type"]) is str
    assert result["nested"][0]["type"] == "MESSAGE_SENT"
    assert result["when"] == payload["when"], "datetimes must reach asyncpg untouched"
