"""Fixture loading helpers for the Event Router test suite.

`events.json` holds DTO-shaped payloads that validate through `Event`. `loops_active.json`
holds row-shaped candidate loops as the router will receive them from the persistence layer,
so those are returned as plain dicts and deliberately not validated here.

Note that `Event` carries no `user_id`, so none of these fixtures encode one. Tests that
exercise the multi-tenant trap (`loop_other_user_500`) must pass the acting user id to the
router explicitly.

Top-level keys in `events.json` prefixed with `NOTE_KEY_PREFIX` are commentary describing how
a group of fixtures relates, not fixtures themselves. They live beside the fixtures rather
than inside them because `Event` sets `extra="forbid"` and would reject an unknown key in an
event payload. Everything here filters them out.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.graph.schemas import Event

FIXTURES_DIR = Path(__file__).parent
EVENTS_PATH = FIXTURES_DIR / "events.json"
LOOPS_ACTIVE_PATH = FIXTURES_DIR / "loops_active.json"

ACTING_USER_ID = "user_001"
NOTE_KEY_PREFIX = "_"


@lru_cache(maxsize=1)
def _events_file() -> dict[str, Any]:
    return json.loads(EVENTS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _raw_events() -> dict[str, Any]:
    return {
        name: payload
        for name, payload in _events_file().items()
        if not name.startswith(NOTE_KEY_PREFIX)
    }


def notes() -> dict[str, str]:
    """The commentary keys from events.json, keyed by note name."""
    return {
        name: body
        for name, body in _events_file().items()
        if name.startswith(NOTE_KEY_PREFIX)
    }


@lru_cache(maxsize=1)
def _raw_loops() -> dict[str, Any]:
    return json.loads(LOOPS_ACTIVE_PATH.read_text(encoding="utf-8"))


def event_names() -> list[str]:
    """Every fixture name available in events.json, in file order."""
    return list(_raw_events())


def load_event(name: str) -> Event:
    """Return one event fixture, freshly validated so callers cannot share mutable state."""
    payloads = _raw_events()
    if name not in payloads:
        raise KeyError(f"Unknown event fixture {name!r}. Available: {sorted(payloads)}")
    return Event.model_validate(payloads[name])


def load_all_events() -> dict[str, Event]:
    """Every event fixture, keyed by fixture name. Useful for parametrized tests."""
    return {name: Event.model_validate(payload) for name, payload in _raw_events().items()}


def load_loop_rows() -> list[dict[str, Any]]:
    """Candidate loop rows as the router receives them: raw, row-shaped, not DTOs."""
    return json.loads(json.dumps(_raw_loops()["loops"]))
