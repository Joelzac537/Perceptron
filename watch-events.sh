#!/usr/bin/env bash
# Follow events as they arrive, in real time.
#
#   ./watch-events.sh          events only  (default)
#   ./watch-events.sh --all    everything, including uvicorn request lines
#
# Safe to run in a second terminal while the server runs in the first, and
# safe to start before the server -- it waits for the log to appear.
cd "$(dirname "$0")" || exit 1

LOG="backend/app/data/server.log"

# --retry so this survives the log being deleted between runs, and -n 20 so
# you get a little history instead of a blank screen while nothing happens.
follow() { tail -n 20 -F "$LOG" 2>/dev/null; }

if [[ "$1" == "--all" ]]; then
    follow
    exit
fi

# Event lines start with [app]. The rest are poller state changes worth
# seeing -- a silent screen during a rate-limit backoff looks like a hang.
# --line-buffered is the whole point: without it grep blocks output and
# "real time" becomes "whenever 4KB accumulates".
follow | grep --line-buffered -E '^\[|baselined|rate limited|recovered|poll failed'
