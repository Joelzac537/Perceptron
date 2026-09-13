"""Polls all four apps and emits Events.

Polling, not Composio triggers, for every app:

    Gmail     Composio's "triggers" for these are pollers running on
    Drive     Composio's servers with a one-minute floor -- slower than
    Calendar  doing it ourselves at 10s.

    Slack     Its Composio trigger is a real webhook and would arrive in
              about a second, but it needs a publicly reachable URL. That
              means running a tunnel beside the server, and a tunnel that
              drops takes Slack offline silently. Polling has no such
              dependency -- one less thing to fail mid-demo.

Each app has its own interval because their rate limits differ by an order of
magnitude, and adapts from there: a throttled app doubles its wait until it
stops being refused, a healthy one walks back down.
"""

import asyncio

from app.events import classify
from app.events.normalizer import normalize
from app.events.sink import emit, mark_seen, save_seen, seen
from app.integrations import composio

MAX_INTERVAL = 300.0     # never back off further than 5 minutes
BACKOFF = 2.0            # multiplier after a throttled call
RECOVER = 0.8            # multiplier after a clean call

# (label, toolkit, classifier, fetch fn, baseline passes, target interval)
# The classifier decides created/updated/deleted per item -- see classify.py.
#
# Every app targets 10s. Slack manages that only because it reads through
# search.messages -- conversations.history is capped near one call a MINUTE
# and would sit permanently backed off. See composio.fetch_recent_slack.
#
# Drive baselines like the rest: its listings return every existing file and
# everything already in the bin, which would otherwise all replay as new.
SOURCES = [
    ("gmail", "GMAIL", classify.gmail, composio.fetch_recent_emails, lambda: 1, 10.0),
    ("drive", "GOOGLEDRIVE", classify.drive, composio.fetch_recent_drive, lambda: 1, 10.0),
    ("calendar", "GOOGLECALENDAR", classify.calendar, composio.fetch_recent_calendar, lambda: 1, 10.0),
    ("slack", "SLACK", classify.slack, composio.fetch_recent_slack, lambda: 1, 10.0),
]


def is_throttled(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("429", "ratelimit", "rate limit", "quota", "403", "too many")
    )


async def watch(label: str, toolkit: str, classifier, fetch, baseline_passes, base: float) -> None:
    # A cold start records what already exists without emitting it, otherwise
    # launching the service would replay the entire inbox as new.
    passes_left = baseline_passes() if not seen else 0
    baselined = 0

    interval = base
    complaining = False  # log a throttle once per episode, not every pass

    while True:
        try:
            items = await asyncio.to_thread(fetch)
        except Exception as exc:
            if is_throttled(exc):
                interval = min(interval * BACKOFF, MAX_INTERVAL)
                if not complaining:
                    print(f"{label}: rate limited, backing off to {interval:.0f}s")
                    complaining = True
            else:
                print(f"{label} poll failed: {str(exc)[:120]}")
            await asyncio.sleep(interval)
            continue

        if complaining:
            print(f"{label}: recovered, polling every {interval:.0f}s")
            complaining = False

        # Oldest first, so Events arrive in the order things actually happened.
        for item in reversed(items):
            # Per item, because one malformed payload must not take the whole
            # app offline. This loop sits outside the fetch try/except above,
            # so an error here used to escape into the task and kill it --
            # silently, since nothing awaits these tasks. The app then just
            # stopped reporting, with no error anywhere to explain why.
            try:
                event = normalize(toolkit, classifier(item), item, source="poll")
            except Exception as exc:
                print(f"{label}: could not normalize an item: {str(exc)[:120]}")
                continue

            if passes_left > 0:
                mark_seen(event)
                baselined += 1
            else:
                await emit(event)

        if passes_left > 0:
            passes_left -= 1
            save_seen()
            if passes_left == 0:
                print(f"  {label}: baselined {baselined} existing item(s)")

        # Creep back towards the target now that the API is answering.
        interval = max(base, interval * RECOVER)
        await asyncio.sleep(interval)


def _report_death(label: str):
    """Say so loudly if a watcher ever stops.

    Nothing awaits these tasks, so an escaped exception is swallowed by
    asyncio and the app simply goes quiet -- which reads as "nothing is
    happening in Drive" rather than "Drive polling is dead". One silent
    failure like that cost an afternoon.
    """
    def done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            print(f"!! {label} watcher DIED: {type(exc).__name__}: {str(exc)[:150]}")

    return done


def start() -> list[asyncio.Task]:
    """One task per app, so a slow or throttled app cannot block the others."""
    tasks = []
    for label, toolkit, classifier, fetch, baseline_passes, base in SOURCES:
        task = asyncio.create_task(
            watch(label, toolkit, classifier, fetch, baseline_passes, base)
        )
        task.add_done_callback(_report_death(label))
        tasks.append(task)

    targets = ", ".join(f"{label} {base:.0f}s" for label, *_, base in SOURCES)
    print(f"  polling {targets} (backs off when throttled)")
    return tasks
