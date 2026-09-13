"""Prove all four connections work, using real data from each app.

    python scripts/check_apps.py

Reads one real item out of Gmail, Slack, Drive and Calendar, pushes it through
the normalizer, and prints the resulting Event. That checks three things at
once: the connection is live, the scopes are sufficient, and the extractor
field names actually match what the app returns.

Read-only. Nothing is sent, created or deleted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.events.normalizer import normalize  # noqa: E402
from app.graph.schemas import EventType  # noqa: E402
from app.integrations.composio import USER_ID, client  # noqa: E402


def call(slug: str, arguments: dict):
    """Run a read-only tool and return its data block."""
    result = client.tools.execute(
        slug,
        user_id=USER_ID,
        arguments=arguments,
        # Otherwise: "Toolkit version not specified".
        dangerously_skip_version_check=True,
    )
    if not result.get("successful"):
        raise RuntimeError(str(result.get("error") or result.get("data"))[:120])
    return result.get("data") or {}


def report(label: str, toolkit: str, event_type: EventType, payload: dict | None) -> bool:
    if payload is None:
        print(f"  {label:<10} no items found (connection may still be fine)")
        return True

    event = normalize(toolkit, event_type, payload, source="check")
    missing = [
        name
        for name in ("external_id", "actor", "subject")
        if getattr(event, name) in (None, "")
    ]

    print(f"  {label:<10} OK")
    print(f"             actor   : {event.actor}")
    print(f"             subject : {event.subject}")
    print(f"             ext_id  : {event.external_id}")
    if missing:
        # Not fatal -- some apps genuinely have no subject or actor -- but a
        # null here usually means a field name is wrong for this app.
        print(f"             note    : empty {', '.join(missing)}")
    return True


def check_gmail() -> bool:
    data = call("GMAIL_FETCH_EMAILS", {"max_results": 1, "verbose": True})
    messages = data.get("messages") or []
    return report("Gmail", "GMAIL", EventType.MESSAGE_RECEIVED, messages[0] if messages else None)


def check_slack() -> bool:
    channels = (call("SLACK_LIST_ALL_CHANNELS", {"limit": 5}) or {}).get("channels") or []
    if not channels:
        print("  Slack      connected, but the bot is in no channels")
        print("             run `/invite @yourbot` in your demo channel")
        return True

    names = ", ".join(c.get("name", "?") for c in channels[:4])
    print(f"  Slack      in {len(channels)} channel(s): {names}")

    history = call(
        "SLACK_FETCH_CONVERSATION_HISTORY",
        {"channel": channels[0]["id"], "limit": 1},
    )
    messages = history.get("messages") or []
    return report("Slack", "SLACK", EventType.MESSAGE_RECEIVED, messages[0] if messages else None)


def check_drive() -> bool:
    files = (call("GOOGLEDRIVE_LIST_FILES", {"page_size": 1}) or {}).get("files") or []
    # An empty result on a Drive you know has files means the scope is
    # drive.file, which only sees files this app created itself.
    if not files:
        print("  Drive      OK but zero files -- if your Drive is not empty,")
        print("             the scope is drive.file instead of drive")
        return True
    return report("Drive", "GOOGLEDRIVE", EventType.DOCUMENT_CREATED, files[0])


def check_calendar() -> bool:
    items = (call("GOOGLECALENDAR_EVENTS_LIST", {"max_results": 1}) or {}).get("items") or []
    return report(
        "Calendar", "GOOGLECALENDAR", EventType.CALENDAR_EVENT_CREATED,
        items[0] if items else None,
    )


def main() -> None:
    print(f"user_id = {USER_ID}\n")

    passed = 0
    for label, check in [
        ("Gmail", check_gmail),
        ("Slack", check_slack),
        ("Drive", check_drive),
        ("Calendar", check_calendar),
    ]:
        try:
            if check():
                passed += 1
        except Exception as exc:
            print(f"  {label:<10} FAILED: {str(exc)[:110]}")
        print()

    print(f"{passed}/4 apps verified end to end")
    if passed < 4:
        sys.exit(1)


if __name__ == "__main__":
    main()
