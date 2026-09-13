#!/usr/bin/env bash
# Start the ingestion service.
#
# Uses the venv interpreter by absolute path on purpose: `python` here
# resolves to conda and `python3` to homebrew, neither of which has composio
# installed. Run from anywhere.
#
# Output goes to your terminal AND to backend/app/data/server.log, so events
# are still readable afterwards if this was started in the background.
cd "$(dirname "$0")/backend" || exit 1

LOG="app/data/server.log"
mkdir -p app/data

# stdbuf keeps the event lines flowing into the log immediately -- without it
# Python block-buffers when stdout is a pipe and `tail -f` shows nothing for
# a long time. PYTHONUNBUFFERED does the same job if stdbuf is missing.
export PYTHONUNBUFFERED=1

exec ../perceptron_env/bin/python -m uvicorn app.main:app \
    --reload --port "${PORT:-8000}" 2>&1 | tee -a "$LOG"
