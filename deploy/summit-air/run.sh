#!/bin/sh
# Start any of the three services that isn't running: the mock ServiceTitan, the tool server and
# the LiveKit worker. Run from cron at boot and by the watchdog; safe to run at any time.
set -u
APP="$HOME/summit-air/app"
LOGS="$HOME/summit-air/logs"
mkdir -p "$LOGS"
cd "$APP" || exit 1
export PATH="$HOME/.local/bin:$PATH"

# Matched by port or by this checkout's path, because other stacks (the Retell baseline) run the
# same programs on this server.
running() { pgrep -u "$(id -u)" -f "$1" >/dev/null; }
launch() { log="$1"; shift; setsid nohup uv run --env-file ../.env "$@" >>"$LOGS/$log" 2>&1 </dev/null & }

if ! running "servicetitan_mock.app.*--port 8101"; then
  test -f data/servicetitan_mock.db || uv run --env-file ../.env python -m servicetitan_mock.seed >>"$LOGS/mock.log" 2>&1
  launch mock.log uvicorn servicetitan_mock.app:create_app --factory --host 127.0.0.1 --port 8101
fi
running "app.main.*--port 8100" || launch tool-server.log uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8100
running "voice.livekit.worker start" || launch worker.log python -m voice.livekit.worker start
exit 0
