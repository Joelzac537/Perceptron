"""Run the real pipeline against the real model, using your .env API key.

    .venv\\Scripts\\python.exe run_live.py            (3 scenarios, ~6 model calls)
    .venv\\Scripts\\python.exe run_live.py --all      (all 6 scenarios)
    .venv\\Scripts\\python.exe run_live.py --check    (one cheap call, just tests the key)

This uses the real Event Router, the real Evidence Verifier and the real Outcome
Compiler. The only thing still faked is the database: writes go to an in-memory recorder
so you do not need Postgres or Supabase running.

It costs real tokens. Start with --check.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from fixtures.router import ACTING_USER_ID, load_event, load_loop_rows  # noqa: E402

from app.agents.compiler import OutcomeCompiler  # noqa: E402
from app.agents.llm import LLMError, OpenAIProvider, ReasoningBoundary  # noqa: E402
from app.agents.replanner import Replanner  # noqa: E402
from app.agents.verifier import EvidenceVerifier  # noqa: E402
from app.config import Settings  # noqa: E402
from app.events.hydration import FixtureLoopGraphSource  # noqa: E402
from app.events.repository import FixtureLoopRepository  # noqa: E402
from app.events.router import EventRouter  # noqa: E402
from app.graph.state import InMemoryRuntimeStore  # noqa: E402
from app.graph.workflow import EventWorkflow  # noqa: E402
from run_demo import graph_rows, presentation_graph_rows  # noqa: E402

ENV_FILE = ROOT / ".env"
NOW = datetime.now(UTC)
WIDTH = 78

SCENARIOS = [
    ("refund_confirmed_no_thread", "A refund email for an order we already track"),
    ("mike_has_final_no_thread", "A Slack message with no order number (needs judgement)"),
    ("newsletter", "A newsletter that says 'refund' and 'insurance' but means neither"),
    ("new_obligation", "A bank demanding proof of address by September 30"),
    ("ambiguous_insurance", "An insurance request that fits two loops equally"),
    ("completed_loop_refund", "A late refund for a loop that is already COMPLETED"),
]


def banner(text: str) -> None:
    print("\n" + "=" * WIDTH)
    print(f" {text}")
    print("=" * WIDTH)


def show(state, store, seconds: float) -> None:
    print(f"\n  decided in {seconds:.1f}s:")
    matches = state.get("matches") or []
    if matches:
        for match in matches:
            print(f"    MATCHED {match.loop_id}  (confidence {match.confidence})")
            print(f"      because: {match.reason}")
    elif state.get("create_new_loop"):
        print("    NEW OBLIGATION -> compiled a new loop")
        print(f"      loop id: {state.get('compiled_loop_id')}")
    else:
        print("    no match, no new obligation. Ignored.")

    for loop_id, verification in (state.get("verifications") or {}).items():
        print(f"    verified {loop_id}:")
        for decision in verification.decisions:
            print(f"      - {decision.node_id}: {decision.relationship} "
                  f"({decision.confidence}) {decision.reason[:52]}")

    for error in state.get("errors") or []:
        print(f"    ! {error}")
    print(f"    writes: {' -> '.join(store.call_names)}")


async def check_key(settings: Settings) -> bool:
    """One minimal call, so a bad key or model name fails fast and readably."""
    from app.events.router_models import RouteDraft
    from app.graph.schemas import Event

    print("  calling the model once to check credentials and model name...")
    async with OpenAIProvider(settings) as provider:
        router = EventRouter(
            repo=FixtureLoopRepository(load_loop_rows()),
            llm=provider,
            user_id=ACTING_USER_ID,
            now_fn=lambda: NOW,
        )
        from app.graph.schemas import RouteEventRequest

        event: Event = load_event("newsletter")
        try:
            response = await router.route(RouteEventRequest(event=event))
        except LLMError as exc:
            print(f"\n  FAILED: {exc.code}")
            print(f"  {exc}")
            return False
    print(f"  OK. The model answered. (matches: {len(response.matches)})")
    print(f"  RouteDraft schema accepted: {RouteDraft.__name__}")
    return True


async def run(names: list[str], settings: Settings) -> None:
    async with OpenAIProvider(settings) as provider:
        boundary = ReasoningBoundary(provider, settings, clock=lambda: NOW)
        verifier = EvidenceVerifier(boundary)
        compiler = OutcomeCompiler(boundary)
        replanner = Replanner(boundary)

        for index, name in enumerate(names, start=1):
            title = dict(SCENARIOS)[name]
            print(f"\n\n>>> {index}. {title}")
            print("-" * WIDTH)
            event = load_event(name)
            body = (event.content or "").replace("\n", " ").strip()
            print(f"  event : {event.id} ({event.source_app} / {event.event_type.value})")
            print(f"  says  : {body[:62]}{'...' if len(body) > 62 else ''}")

            store = InMemoryRuntimeStore()
            workflow = EventWorkflow(
                router=EventRouter(
                    repo=FixtureLoopRepository(load_loop_rows()),
                    llm=provider,
                    user_id=ACTING_USER_ID,
                    now_fn=lambda: NOW,
                ),
                graphs=FixtureLoopGraphSource([graph_rows(), presentation_graph_rows()]),
                store=store,
                user_id=ACTING_USER_ID,
                verifier=verifier,
                replanner=replanner,
                compiler=compiler,
                available_apps=["gmail", "slack", "google_drive", "google_calendar", "loopgraph"],
            )

            started = asyncio.get_event_loop().time()
            try:
                state = await workflow.run(event)
            except LLMError as exc:
                print(f"\n  MODEL ERROR: {exc.code} - {exc}")
                print(f"  attempts: {[a.outcome for a in exc.attempts]}")
                continue
            show(state, store, asyncio.get_event_loop().time() - started)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="run all six scenarios")
    parser.add_argument("--check", action="store_true", help="one call, just verify the key")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        print(f"No .env at {ENV_FILE}. Copy .env.example and add OPENAI_API_KEY.")
        return 1

    settings = Settings.from_env(env_file=ENV_FILE)
    banner("LoopGraph - LIVE run (real model, in-memory database)")
    print(f"\n  model    : {settings.model}")
    print(f"  timezone : {settings.timezone}")
    print(f"  api key  : {'loaded from .env' if settings.api_key else 'MISSING'}")
    if not settings.api_key:
        print("\n  Add OPENAI_API_KEY to your .env and try again.")
        return 1

    if args.check:
        banner("Credential check")
        return 0 if await check_key(settings) else 1

    names = [name for name, _ in SCENARIOS] if args.all else [n for n, _ in SCENARIOS[:3]]
    print(f"  running  : {len(names)} scenarios (this costs real tokens)")
    await run(names, settings)
    banner("Live run complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
