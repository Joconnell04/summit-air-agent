#!/bin/sh
# Every minute from cron: restart what stopped, post the worker heartbeat, ping the outside monitor,
# and move the phone number to the recorded fallback after two failed worker checks in a row (and
# back once it passes). docs/architecture.md, hosting.
set -u
BASE="$HOME/summit-air"
STATE="$BASE/watchdog.state"
cd "$BASE/app" || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . "$BASE/.env"; set +a

sh deploy/summit-air/run.sh

# The worker's own health page answers 200 only once it is registered with LiveKit.
if curl -fsS -m 5 http://127.0.0.1:8081/ >/dev/null 2>&1 \
   && curl -fsS -m 5 http://127.0.0.1:8100/healthz >/dev/null 2>&1; then
  curl -fsS -m 5 -X POST -H 'Content-Type: application/json' -d '{"worker":"livekit"}' \
    http://127.0.0.1:8100/healthz/heartbeat >/dev/null 2>&1
  [ -n "${HEALTHCHECK_URL:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_URL" >/dev/null 2>&1
  if [ "$(cat "$STATE" 2>/dev/null)" = "fallback" ]; then
    uv run python -m scripts.phone.fallback off >>"$BASE/logs/watchdog.log" 2>&1
  fi
  echo 0 >"$STATE"
else
  failures=$(cat "$STATE" 2>/dev/null)
  case "$failures" in fallback) exit 0 ;; ''|*[!0-9]*) failures=0 ;; esac
  failures=$((failures + 1))
  echo "$(date -u +%FT%TZ) worker check failed ($failures)" >>"$BASE/logs/watchdog.log"
  if [ "$failures" -ge 2 ] && uv run python -m scripts.phone.fallback on >>"$BASE/logs/watchdog.log" 2>&1; then
    failures=fallback
  fi
  echo "$failures" >"$STATE"
fi
