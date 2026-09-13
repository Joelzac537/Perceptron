"""Watch the LoopGraph event pipeline run, end to end.

Run me with:

    .venv\\Scripts\\python.exe run_demo.py          (Windows)
    ./.venv/bin/python run_demo.py                  (Mac/Linux)

No database, no API key, no internet. The agents are stubs and the "database" is an
in-memory recorder, so what you see is the real routing logic and the real graph
wiring, with the expensive parts faked.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from fixtures.router import ACTING_USER_ID, load_event, load_loop_rows  # noqa: E402
from provider_helpers import FakeProvider  # noqa: E402

from app.events.hydration import FixtureLoopGraphSource  # noqa: E402
from app.events.repository import FixtureLoopRepository, UnavailableLLM  # noqa: E402
from app.events.router import EventRouter  # noqa: E402
from app.events.router_models import RouteDraft  # noqa: E402
from app.graph.schemas import CompiledGraph, NodeEvidenceDecision, VerifyEventResponse  # noqa: E402
from app.graph.state import InMemoryRuntimeStore  # noqa: E402
from app.graph.workflow import EventWorkflow  # noqa: E402

FIXTURES = ROOT / "backend" / "tests" / "fixtures"
NOW = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)
WIDTH = 78


# --------------------------------------------------------------------------------------
# Stand-ins for the real agents, so the demo needs no API key.
# --------------------------------------------------------------------------------------


class StubVerifier:
    """Pretends to be the Evidence Verifier."""

    async def verify(self, request) -> VerifyEventResponse:
        return VerifyEventResponse(
            decisions=[
                NodeEvidenceDecision(
                    node_id=node.id,
                    relationship="PARTIALLY_SUPPORTS",
                    confidence=0.7,
                    reason="demo stub: looks related but not conclusive",
                )
                for node in request.nodes
            ],
            requires_replan=False,
        )


class StubCompiler:
    """Pretends to be the Outcome Compiler."""

    def __init__(self) -> None:
        self.graph = CompiledGraph.model_validate(
            json.loads((FIXTURES / "promise_compiled_graph.json").read_text("utf-8"))
        )

    async def compile(self, request) -> CompiledGraph:
        return self.graph


def graph_rows() -> dict:
    graph = json.loads((FIXTURES / "refund_compiled_graph.json").read_text("utf-8"))
    return {
        "loop": graph["loop"],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "requirements": graph["evidence_requirements"],
        "actions": [],
        "evidence": [],
        "state_revision": "rev-1",
    }


def presentation_graph_rows() -> dict:
    """A small hand-built graph for the Slack scenario, so it can be verified too."""
    stamp = "2026-09-14T09:05:00-04:00"
    return {
        "loop": {
            "id": "loop_presentation_001",
            "user_id": ACTING_USER_ID,
            "title": "Send the final client deck",
            "goal": "Obtain the final client presentation and send it before the deadline.",
            "status": "ACTIVE",
            "root_node_id": "node_presentation_send",
            "created_at": stamp,
            "updated_at": stamp,
        },
        "nodes": [
            {
                "id": "node_presentation_send",
                "loop_id": "loop_presentation_001",
                "title": "Send the final deck to the client",
                "status": "WAITING",
                "created_at": stamp,
                "updated_at": stamp,
            },
            {
                "id": "node_presentation_collect",
                "loop_id": "loop_presentation_001",
                "title": "Get the final deck from Sarah",
                "status": "WAITING",
                "owner": "Sarah",
                "metadata": {"document_type": "presentation", "version": "final"},
                "created_at": stamp,
                "updated_at": stamp,
            },
        ],
        "edges": [
            {
                "id": "edge_presentation_1",
                "loop_id": "loop_presentation_001",
                "source_node_id": "node_presentation_send",
                "target_node_id": "node_presentation_collect",
                "relationship": "DEPENDS_ON",
                "created_at": stamp,
            }
        ],
        "requirements": [
            {
                "id": "req_presentation_file",
                "node_id": "node_presentation_collect",
                "type": "DOCUMENT",
                "description": "The final version of the client presentation.",
                "source_apps": ["slack", "google_drive"],
                "required_fields": {"version": "final"},
                "must_all_match": True,
                "created_at": stamp,
            }
        ],
        "actions": [],
        "evidence": [],
        "state_revision": "rev-1",
    }


def make_workflow(store: InMemoryRuntimeStore, semantic: RouteDraft | None = None):
    """Build a pipeline. `semantic` stands in for the model's answer when the
    deterministic stages cannot decide on their own."""
    router = EventRouter(
        repo=FixtureLoopRepository(load_loop_rows()),
        # UnavailableLLM raises if anything tries to call a model, which is how we prove
        # the fast paths never do.
        llm=FakeProvider(outputs=[semantic]) if semantic else UnavailableLLM(),
        user_id=ACTING_USER_ID,
        now_fn=lambda: NOW,
    )
    return EventWorkflow(
        router=router,
        graphs=FixtureLoopGraphSource([graph_rows(), presentation_graph_rows()]),
        store=store,
        user_id=ACTING_USER_ID,
        verifier=StubVerifier(),
        compiler=StubCompiler(),
        available_apps=["gmail", "slack", "google_drive", "google_calendar", "loopgraph"],
    )


# --------------------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------------------


def banner(text: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {text}")
    print("=" * WIDTH)


def section(number: int, title: str) -> None:
    print(f"\n\n>>> SCENARIO {number}: {title}")
    print("-" * WIDTH)


def show_event(event) -> None:
    body = (event.content or "").replace("\n", " ").strip()
    print(f"  incoming event : {event.id}")
    print(f"  from           : {event.source_app} / {event.event_type.value}")
    print(f"  says           : {body[:62]}{'...' if len(body) > 62 else ''}")


def show_result(state, store: InMemoryRuntimeStore) -> None:
    print("\n  what the pipeline decided:")
    if state.get("duplicate"):
        print("    - already seen this exact event, stopped immediately")
    else:
        matches = state.get("matches") or []
        if matches:
            for match in matches:
                print(f"    - MATCHED {match.loop_id}  (confidence {match.confidence})")
                print(f"        because: {match.reason}")
        elif state.get("create_new_loop"):
            print("    - no existing loop fits; this is a NEW obligation")
            print(f"        created loop: {state.get('compiled_loop_id')}")
        else:
            print("    - related to nothing, and creates no obligation. Ignored.")

        for loop_id, verification in (state.get("verifications") or {}).items():
            print(f"    - verified {loop_id}: {len(verification.decisions)} node decisions")

    for error in state.get("errors") or []:
        print(f"    ! {error}")

    print("\n  database writes it would make, in order:")
    for index, name in enumerate(store.call_names, start=1):
        print(f"    {index}. {name}")


# --------------------------------------------------------------------------------------


async def main() -> None:
    banner("LoopGraph pipeline demo - no database, no API key, no internet")
    print("\n  Six loops are already being tracked for user_001. Watch how each")
    print("  incoming event is routed, verified, and recorded.")

    # 1. An event that clearly belongs to an existing loop.
    section(1, "A refund email about an order we are already tracking")
    store = InMemoryRuntimeStore()
    workflow = make_workflow(store)
    event = load_event("refund_confirmed_no_thread")
    show_event(event)
    show_result(await workflow.run(event), store)
    print("\n  ^ Notice: no model was consulted. The order number was enough.")

    # 2. The very same event arriving twice.
    section(2, "The exact same email delivered a second time")
    store.calls.clear()
    show_event(event)
    show_result(await workflow.run(event), store)
    print("\n  ^ Notice: one check, then it stopped. No duplicate evidence written.")

    # 3. Something that needs judgement, not pattern matching.
    section(3, "A Slack message with no order number anywhere")
    store = InMemoryRuntimeStore()
    semantic = RouteDraft(
        candidates=[
            {
                "loop_id": "loop_presentation_001",
                "confidence": 0.86,
                "reason": "Sarah owns the open outcome and is handing the deck to Mike.",
            }
        ],
        is_new_obligation=False,
        obligation_reason=None,
    )
    event = load_event("mike_has_final_no_thread")
    show_event(event)
    show_result(await make_workflow(store, semantic).run(event), store)
    print("\n  ^ Notice: nothing matched literally, so the model was asked.")

    # 4. A brand new obligation.
    section(4, "A bank demanding proof of address by September 30")
    store = InMemoryRuntimeStore()
    semantic = RouteDraft(
        candidates=[],
        is_new_obligation=True,
        obligation_reason="Bank requires proof of address by September 30.",
    )
    event = load_event("new_obligation")
    show_event(event)
    show_result(await make_workflow(store, semantic).run(event), store)
    print("\n  ^ Notice: no loop fit, so a new one gets compiled.")

    # 5. Noise.
    section(5, "A marketing newsletter that happens to say 'refund' and 'insurance'")
    store = InMemoryRuntimeStore()
    semantic = RouteDraft(candidates=[], is_new_obligation=False, obligation_reason=None)
    event = load_event("newsletter")
    show_event(event)
    show_result(await make_workflow(store, semantic).run(event), store)
    print("\n  ^ Notice: the words matched, the meaning did not. Nothing was created.")

    # 6. A trap: a refund for a loop that is already finished.
    section(6, "A late refund email for a loop that is already COMPLETED")
    store = InMemoryRuntimeStore()
    semantic = RouteDraft(candidates=[], is_new_obligation=False, obligation_reason=None)
    event = load_event("completed_loop_refund")
    show_event(event)
    show_result(await make_workflow(store, semantic).run(event), store)
    print("\n  ^ Notice: a finished loop is never reopened by a late event.")

    banner("Demo complete")
    print("\n  Next: run the test suite to check all 459 tests still pass:")
    print("      .venv\\Scripts\\python.exe -m pytest -q\n")


if __name__ == "__main__":
    asyncio.run(main())
