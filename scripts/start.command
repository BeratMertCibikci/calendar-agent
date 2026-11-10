#!/bin/bash
# macOS double-click launcher: activates the venv, starts the server and opens the browser.
cd "$(dirname "$0")/.." || exit 1
[ -d .venv ] && source .venv/bin/activate
( sleep 2 && open "http://127.0.0.1:${PORT:-5001}" ) &
calendar-agent
