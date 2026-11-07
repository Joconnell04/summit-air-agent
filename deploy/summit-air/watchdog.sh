#!/bin/sh
# Every minute from cron: restart what stopped, post the worker heartbeat, ping the outside monitor,
# and, only with PHONE_FALLBACK=on in .env, move the phone number to the recorded fallback after two
# failed worker checks in a row (and back once it passes). docs/architecture.md, hosting.
# Off by default since the number moved to Retell (DECISIONS.md 112): the fallback's "back once it
# passes" puts the number on the LiveKit trunk, whatever it was routed to.
set -u
BASE="$HOME/summit-air"
STATE="$BASE/watchdog.state"
cd "$BASE/app" || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . "$BASE/.env"; set +a

sh deploy/summit-air/run.sh

# The phone path is up when the tool server answers its health page and the Retell socket that
# answers the demo number (port 8114, a systemd unit) answers at all; it has no health page, so
# any HTTP status counts.
socket=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8114/ 2>/dev/null)
if curl -fsS -m 5 http://127.0.0.1:8100/healthz >/dev/null 2>&1 && [ "${socket:-000}" != 000 ]; then
  curl -fsS -m 5 -X POST -H 'Content-Type: application/json' -d '{"worker":"retell"}' \
    http://127.0.0.1:8100/healthz/heartbeat >/dev/null 2>&1
  [ -n "${HEALTHCHECK_URL:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_URL" >/dev/null 2>&1
  if [ "${PHONE_FALLBACK:-off}" = on ] && [ "$(cat "$STATE" 2>/dev/null)" = "fallback" ]; then
    uv run python -m scripts.phone.fallback off >>"$BASE/logs/watchdog.log" 2>&1
  fi
  echo 0 >"$STATE"
else
  failures=$(cat "$STATE" 2>/dev/null)
  case "$failures" in fallback) exit 0 ;; ''|*[!0-9]*) failures=0 ;; esac
  failures=$((failures + 1))
  echo "$(date -u +%FT%TZ) phone path check failed ($failures)" >>"$BASE/logs/watchdog.log"
  if [ "${PHONE_FALLBACK:-off}" = on ] && [ "$failures" -ge 2 ] \
     && uv run python -m scripts.phone.fallback on >>"$BASE/logs/watchdog.log" 2>&1; then
    failures=fallback
  fi
  echo "$failures" >"$STATE"
fi
