"""Gmail fast path.

Composio's Gmail trigger is a POLL trigger whose interval is measured in
minutes, which is too slow for a live demo. This asks Gmail directly on our
own clock instead.

No Gmail trigger is registered anywhere -- if one were, every email would
arrive twice, once here and once through the webhook.
"""

import asyncio
import os

from app.events.normalizer import normalize
from app.events.sink import emit, mark_seen, save_seen, seen
from app.graph.schemas import EventType
from app.integrations.composio import fetch_recent_emails

POLL_SECONDS = float(os.getenv("POLL_SECONDS", "5"))


async def run() -> None:
    # An empty seen-set means a cold start: record what is already in the
    # inbox without emitting, or launching the service replays old mail.
    first_run = not seen

    while True:
        try:
            messages = await asyncio.to_thread(fetch_recent_emails, 5)
        except Exception as exc:
            print(f"gmail poll failed: {str(exc)[:140]}")
            await asyncio.sleep(POLL_SECONDS)
            continue

        # Oldest first, so Events arrive in the order the mail was sent.
        for message in reversed(messages):
            event = normalize("GMAIL", EventType.MESSAGE_RECEIVED, message, source="poll")
            if first_run:
                mark_seen(event)
            else:
                await emit(event)

        if first_run:
            save_seen()
            print(f"baselined {len(messages)} existing message(s)")
            first_run = False

        await asyncio.sleep(POLL_SECONDS)
