#!/bin/bash
LABEL="com.ps5tracker.mac"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed. (Settings kept in ~/.config/ps5-tracker/env)"
