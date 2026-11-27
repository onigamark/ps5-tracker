#!/bin/bash
# One quiet check of the stores that block GitHub's servers. launchd runs this every 60s.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$HOME/.config/ps5-tracker/env"   # NTFY_TOPIC=..., optional GMAIL_USER / GMAIL_APP_PASSWORD
LOG="$HOME/Library/Logs/ps5-tracker.log"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
# Keep the log small.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt 1000000 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
export TRACKER_NAME="${TRACKER_NAME:-Mac (Walmart + Amazon)}"
exec /usr/bin/python3 "$DIR/tracker.py" --quiet --sources "${TRACKER_SOURCES:-walmart,amazon}" \
  --state "$DIR/state/mac-state.json"
