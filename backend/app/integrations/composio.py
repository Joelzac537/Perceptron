"""Composio client and the four app definitions.

The only module that talks to Composio. Reasoning code must not import it.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
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
# No Composio triggers are used at all -- all four apps are polled directly
# (events/poller.py), so nothing here needs a public URL.
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


def execute(slug: str, arguments: dict) -> dict:
    """Run a Composio tool and return its data block.

    dangerously_skip_version_check is required -- without it Composio errors
    with "Toolkit version not specified", and version="latest" is not accepted.
    """
    result = client.tools.execute(
        slug,
        user_id=USER_ID,
        arguments=arguments,
        dangerously_skip_version_check=True,
    )
    data = result.get("data") or {}
    # A throttled or failed call still returns 200 from Composio with the
    # error tucked inside data. Left alone it looks like "no new items",
    # which hides rate limiting completely.
    if isinstance(data, dict) and data.get("http_error"):
        raise RuntimeError(f"{slug}: {data.get('status_code')} {str(data.get('message'))[:80]}")
    return data


# Gmail has no "changes" concept -- a message's lifecycle is expressed purely
# by which labels it carries. Reading these three boxes is what turns Gmail
# from "new mail only" into full coverage: INBOX alone cannot see a reply you
# sent or a message you deleted.
GMAIL_BOXES = ["INBOX", "SENT", "TRASH"]


def fetch_recent_emails(limit: int = 5) -> list[dict]:
    """Recent Gmail messages from the inbox, sent mail and the bin.

    Newest first within each box. classify.gmail reads labelIds to tell them
    apart, so the caller does not need to know which query produced what.
    """
    messages = []
    for box in GMAIL_BOXES:
        data = execute(
            "GMAIL_FETCH_EMAILS",
            {"max_results": limit, "verbose": True, "label_ids": [box]},
        )
        messages += data.get("messages") or []
    return messages


_slack_users: dict[str, str] = {}


def slack_user_names() -> dict[str, str]:
    """Slack user id -> display name, fetched once and cached.

    Messages only carry the raw id (U0C1HJBGC10). The workflows care about
    "Sarah promised the deck", so the id has to become a name somewhere, and
    doing it here keeps the normalizer free of network calls.
    """
    if not _slack_users:
        try:
            members = (execute("SLACK_LIST_ALL_USERS", {"limit": 200}) or {}).get(
                "members"
            ) or []
            for member in members:
                name = member.get("real_name") or member.get("name")
                if member.get("id") and name:
                    _slack_users[member["id"]] = name
        except Exception as exc:
            print(f"slack user lookup failed: {str(exc)[:90]}")
    return _slack_users


# Last poll's messages, keyed by ts. The only way to notice an edit (text
# changed) or a deletion (message gone), since Slack's search reports neither.
_slack_previous: dict[str, dict] = {}


def fetch_recent_slack(limit: int = 20) -> list[dict]:
    """Recent messages across every channel and DM, newest first, in ONE call.

    search.messages rather than conversations.history. History is limited to
    roughly one request per MINUTE for apps like Composio's, and covers a
    single channel per call -- so polling it meant round-robining the channels
    and a new message could take several minutes to surface. Search sits in a
    much higher rate-limit tier, covers every conversation at once, and picks
    up DMs, which a channel listing never sees.

    `after:` has day granularity, so this re-reads the last two days on every
    pass and lets the dedup key discard what we already have.

    Needs search:read on the connected account. If Slack ever refuses the
    method, that is the scope to check first.

    Edits and deletions are worked out by comparing consecutive polls -- see
    _slack_previous. Slack's search results carry no `edited` marker and a
    deleted message is simply absent, so neither can be read off a single
    response.
    """
    global _slack_previous

    since = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    data = execute(
        "SLACK_SEARCH_MESSAGES",
        {
            "query": f"after:{since.isoformat()}",
            "count": limit,
            "sort": "timestamp",
            "sort_dir": "desc",
        },
    )

    names = slack_user_names()
    messages = []
    current: dict[str, dict] = {}

    for match in ((data.get("messages") or {}).get("matches") or []):
        # Search returns channel as an object; the normalizer wants the id.
        channel = match.get("channel") or {}
        match["channel"] = channel.get("id")
        match["channel_name"] = channel.get("name")
        match["user_id"] = match.get("user")
        if match.get("user") in names:
            match["username"] = names[match["user"]]

        ts = str(match.get("ts") or "")
        previous = _slack_previous.get(ts)
        if previous is not None and previous.get("text") != match.get("text"):
            match["slack_state"] = "edited"

        current[ts] = match
        messages.append(match)

    # A message that has vanished since the last poll was deleted -- but only
    # trust that inside the window we can still see. Results are capped at
    # `count`, so an older message can drop off the bottom simply because
    # newer ones pushed it out, and calling that a deletion would be wrong.
    if current:
        floor = min(float(ts) for ts in current)
        for ts, old in _slack_previous.items():
            if ts in current or float(ts) < floor:
                continue
            messages.append({**old, "slack_state": "deleted"})

    _slack_previous = current
    return messages


DRIVE_TOKEN_FILE = DATA / "drive_page_token"
_drive_token: str | None = None


def _drive_cursor() -> str:
    """Where in Drive's change feed we have read up to.

    First run starts at "now". Google expires old page tokens, so history
    cannot be replayed -- a file permanently deleted before the first run is
    invisible for good.

    Persisted so a restart resumes instead of skipping whatever happened
    while the service was down.
    """
    global _drive_token
    if _drive_token:
        return _drive_token

    if DRIVE_TOKEN_FILE.exists():
        _drive_token = DRIVE_TOKEN_FILE.read_text().strip()
    if not _drive_token:
        data = execute("GOOGLEDRIVE_GET_CHANGES_START_PAGE_TOKEN", {})
        _drive_token = str(data.get("startPageToken") or "")
        DRIVE_TOKEN_FILE.write_text(_drive_token)
    return _drive_token


# Google's changes feed returns a file object with only id/kind/mimeType/name
# and gives no way to ask for more, so anything that depends on trashed or the
# timestamps has to come from LIST_FILES, which does accept a fields mask.
DRIVE_FIELDS = "files(id,name,mimeType,trashed,createdTime,modifiedTime),nextPageToken"

# Media is ignored. A loop is closed by a receipt, an invoice, a signed PDF or
# a spreadsheet -- documents that serve as evidence. Holiday video and voice
# memos only add noise, and they are exactly the files people bulk-upload, so
# one phone sync would otherwise flood the stream and bury the real evidence.
#
# This is a prefix match on the mime type, so it costs nothing to change:
# empty the set and everything comes through.
DRIVE_IGNORED_MEDIA = ("video/", "audio/")


def is_evidence(file: dict) -> bool:
    mime = str(file.get("mimeType") or "")
    return not mime.startswith(DRIVE_IGNORED_MEDIA)


# --- reading file CONTENTS (not enabled) --------------------------------
#
# Today an Event carries a Drive file's name, link and category, not its text.
# That is enough to say "a receipt was uploaded"; it is not enough to say "the
# receipt is for $129". If the Verifier ever needs the second kind of claim,
# uncomment this and the call in fetch_recent_drive.
#
# Verified working before being commented out, so the shape below is real:
#
#   - DOWNLOAD_FILE does NOT return text inline. It returns
#       {"downloaded_file_content": {"mimetype","name","s3url"}}
#     where s3url is a presigned link that expires in one hour, so it has to
#     be fetched separately.
#   - Google Workspace files (Docs, Sheets, Slides) must be EXPORTED by
#     passing mime_type; the response then has export_applied: true. Passing
#     mime_type for a normal file is what makes it a plain download.
#   - PDFs and images come back as bytes. Extracting words from those needs a
#     parser (pypdf, OCR) that is deliberately not a dependency here.
#
# Cost: one Composio call plus one HTTP fetch per file, on a 10s poll. Do it
# lazily -- only for the categories a loop actually reasons about -- rather
# than for everything that changes.
#
# EXPORTABLE = {
#     "application/vnd.google-apps.document": "text/plain",
#     "application/vnd.google-apps.spreadsheet": "text/csv",
#     "application/vnd.google-apps.presentation": "text/plain",
# }
# TEXT_LIKE = ("text/", "application/json")
#
#
# def drive_file_text(file_id: str, mime: str, limit: int = 4000) -> str | None:
#     """Plain text of a Drive file, or None if it is not text at all."""
#     import httpx
#
#     export_as = EXPORTABLE.get(mime)
#     if not export_as and not str(mime).startswith(TEXT_LIKE):
#         return None  # pdf, image, binary -- needs a parser, see above
#
#     args = {"file_id": file_id}
#     if export_as:
#         args["mime_type"] = export_as
#
#     data = execute("GOOGLEDRIVE_DOWNLOAD_FILE", args)
#     url = (data.get("downloaded_file_content") or {}).get("s3url")
#     if not url:
#         return None
#     return httpx.get(url, timeout=30).text[:limit]


def _drive_listing(query: str, limit: int) -> list[dict]:
    data = execute(
        "GOOGLEDRIVE_LIST_FILES",
        {
            "q": query,
            "pageSize": limit,
            "orderBy": "modifiedTime desc",
            "fields": DRIVE_FIELDS,
        },
    )
    return data.get("files") or []


def fetch_recent_drive(limit: int = 25) -> list[dict]:
    """Everything that happened in Drive, newest first.

    Three reads, because no single Drive call reports all three outcomes:

      live files    LIST_FILES q=trashed=false -- creations and edits. Needs
                    the explicit fields mask or createdTime/modifiedTime come
                    back empty and every file looks newly created forever.

      trashed       LIST_FILES q=trashed=true -- "delete" in the Drive UI means
                    move to bin, which leaves the file listed and merely flips
                    trashed. Trashing does NOT reliably bump modifiedTime, so a
                    bin item can sit far down the by-date listing: it has to be
                    queried for separately rather than hoped for.

      changes feed  the only source that reports a PERMANENT delete, where the
                    file is gone and only an id survives.

    Returns file-shaped dicts so the normalizer sees one consistent payload.
    """
    global _drive_token

    # Media is dropped here rather than downstream so it never reaches the
    # dedup store either -- otherwise a bulk photo sync would evict real
    # evidence from the seen-set. See DRIVE_IGNORED_MEDIA.
    items = [f for f in _drive_listing("trashed = false", limit) if is_evidence(f)]
    items += [f for f in _drive_listing("trashed = true", limit) if is_evidence(f)]

    # Pull the file's text into the Event as well. Off by default -- see the
    # drive_file_text block above for why, and what it costs. The normalizer
    # already prefers a "text" key when one is present, so uncommenting these
    # three lines is the whole change.
    #
    # for file in items:
    #     if file.get("mimeType") in EXPORTABLE:
    #         file["text"] = drive_file_text(file["id"], file["mimeType"])

    data = execute(
        "GOOGLEDRIVE_LIST_CHANGES",
        {
            "pageToken": _drive_cursor(),
            "pageSize": 100,
            # Without these two a removal is dropped from the feed, and
            # permanent deletion becomes invisible again.
            "includeRemoved": True,
            "includeCorpusRemovals": True,
        },
    )

    for change in data.get("changes") or []:
        if not change.get("removed"):
            continue  # already covered, with better fields, by the listings
        items.append(
            {
                "id": change.get("fileId"),
                "removed": True,
                "change_time": change.get("time"),
            }
        )

    # Advance only after a successful read, so a crash re-reads rather than
    # skips whatever it was in the middle of.
    next_token = data.get("newStartPageToken") or data.get("nextPageToken")
    if next_token and str(next_token) != _drive_token:
        _drive_token = str(next_token)
        DRIVE_TOKEN_FILE.write_text(_drive_token)

    return items


def fetch_recent_calendar(limit: int = 10, window_minutes: int = 60) -> list[dict]:
    """Calendar events changed in the last `window_minutes`.

    orderBy=updated sorts ASCENDING, so asking for the first N returns the
    OLDEST-updated events and a newly created one never shows up. updatedMin
    is the right tool: ask only for what changed recently.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    data = execute(
        "GOOGLECALENDAR_EVENTS_LIST",
        {
            "maxResults": limit,
            "orderBy": "updated",
            "singleEvents": True,
            "updatedMin": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Deleted events are returned as status=cancelled, but only when
            # showDeleted is set -- otherwise a deletion is simply invisible.
            "showDeleted": True,
        },
    )
    return data.get("items") or []


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
