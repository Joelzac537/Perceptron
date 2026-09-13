"""Composio client and the four app definitions.

The only module that talks to Composio. Reasoning code must not import it.
"""

import os
import sys
from pathlib import Path

from composio import Composio
from dotenv import load_dotenv

# backend/app/integrations/composio.py -> repo root is three levels up
ROOT = Path(__file__).resolve().parents[3]
DATA = Path(__file__).resolve().parents[1] / "data"
DATA.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

API_KEY = os.getenv("COMPOSIO_API_KEY")
USER_ID = os.getenv("USER_ID")

if not API_KEY:
    sys.exit("COMPOSIO_API_KEY missing from .env")
if not USER_ID:
    sys.exit("USER_ID missing from .env")

client = Composio(api_key=API_KEY)

# One entry per app. `scopes` is requested when the auth config is created --
# wrong scopes fail silently, so these are the important lines in this file.
#
# No Composio triggers are registered yet: that needs a public webhook URL.
# Gmail is polled directly instead (events/gmail_poller.py), and the other
# three can be driven through POST /events until the URL exists.
APPS = {
    "GMAIL": {
        "label": "Gmail",
        "env_key": "GMAIL_ACCOUNT_ID",
        # Need both: readonly cannot send, send cannot search.
        "scopes": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    },
    "GOOGLECALENDAR": {
        "label": "Calendar",
        "env_key": "CALENDAR_ACCOUNT_ID",
        # Full scope: we create, update and delete events.
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    },
    "SLACK": {
        "label": "Slack",
        "env_key": "SLACK_ACCOUNT_ID",
        "scopes": [
            "chat:write",
            "channels:history",
            "channels:read",
            "files:read",
            "users:read",
        ],
    },
    "GOOGLEDRIVE": {
        "label": "Drive",
        "env_key": "DRIVE_ACCOUNT_ID",
        # Full drive on purpose. drive.file only sees files we created
        # ourselves, but we need documents the user already had.
        "scopes": ["https://www.googleapis.com/auth/drive"],
    },
}


def active_accounts() -> dict[str, dict]:
    """Newest ACTIVE connected account per app.

    An app can hold several records -- a live one plus abandoned attempts.
    Always prefer ACTIVE, or a dead stub masks a working connection.
    """
    response = client.connected_accounts.list(user_ids=[USER_ID])

    found: dict[str, dict] = {}
    for account in getattr(response, "items", []) or []:
        toolkit = getattr(account, "toolkit", None)
        slug = str(getattr(toolkit, "slug", toolkit) or "").upper()
        status = getattr(account, "status", "UNKNOWN")
        if slug in found and found[slug]["status"] == "ACTIVE" and status != "ACTIVE":
            continue
        found[slug] = {"id": account.id, "status": status}
    return found


def fetch_recent_emails(limit: int = 5) -> list[dict]:
    """Raw Gmail messages, newest first.

    dangerously_skip_version_check is required -- without it Composio errors
    with "Toolkit version not specified", and version="latest" is not accepted.
    """
    result = client.tools.execute(
        "GMAIL_FETCH_EMAILS",
        user_id=USER_ID,
        arguments={"max_results": limit, "verbose": True, "label_ids": ["INBOX"]},
        dangerously_skip_version_check=True,
    )
    return (result.get("data") or {}).get("messages") or []


def update_env(values: dict[str, str]) -> None:
    """Add or update keys in .env, leaving every other line untouched."""
    env_path = ROOT / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    remaining = dict(values)

    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# connected accounts (written by scripts/connect_apps.py)")
        out += [f"{k}={v}" for k, v in remaining.items()]

    env_path.write_text("\n".join(out) + "\n")
