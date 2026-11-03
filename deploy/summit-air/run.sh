#!/bin/sh
# Start any of the three services that isn't running: the mock ServiceTitan, the tool server and
# the LiveKit worker. Run from cron at boot and by the watchdog; safe to run at any time.
set -u
APP="$HOME/summit-air/app"
LOGS="$HOME/summit-air/logs"
mkdir -p "$LOGS"
cd "$APP" || exit 1
export PATH="$HOME/.local/bin:$PATH"

running() { pgrep -u "$(id -u)" -f "$1" >/dev/null; }
launch() { log="$1"; shift; setsid nohup uv run --env-file ../.env "$@" >>"$LOGS/$log" 2>&1 </dev/null & }

if ! running "uvicorn servicetitan_mock"; then
  test -f data/servicetitan_mock.db || uv run --env-file ../.env python -m servicetitan_mock.seed >>"$LOGS/mock.log" 2>&1
  launch mock.log uvicorn servicetitan_mock.app:create_app --factory --host 127.0.0.1 --port 8101
fi
running "uvicorn app.main" || launch tool-server.log uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8100
running "voice.livekit.worker" || launch worker.log python -m voice.livekit.worker start
exit 0
