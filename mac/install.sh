#!/bin/bash
# Installs a launchd job that runs mac/run.sh every 60 seconds while you're logged in.
set -eu
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.ps5tracker.mac"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="$HOME/.config/ps5-tracker/env"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$(dirname "$ENV_FILE")"
if [ ! -f "$ENV_FILE" ]; then
  read -r -p "ntfy topic: " topic
  umask 077; printf 'NTFY_TOPIC=%s\n' "$topic" > "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/mac/run.sh</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/ps5-tracker.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ps5-tracker.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Log: ~/Library/Logs/ps5-tracker.log"
