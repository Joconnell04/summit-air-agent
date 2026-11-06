#!/usr/bin/env bash
# Runs a slate of scripted calls on one phone line, one call at a time, for the side-by-side eval.
#
#   setsid -f bash run_slate.sh <slate> <out_dir> <number> > <out_dir>/runner.log 2>&1 < /dev/null
#
# <number> is +16466240418 or +16783366730. Each slate line is
# "<arm> <agent_id> <world> <persona> <reset>". <world> names the tool server and mock the call
# runs against (see world() below); two lines running at once must use different worlds. Per line:
# wait until no call is live in that world (no end in the last 30 minutes, as reset_live_world checks), reset the world if <reset> is "reset" ("keep" for a
# follow-up call, such as b moving the visit e booked), point the number at the agent and the
# world's inbound webhook, dial the persona, put the number back as it was, then dump the world's
# rows for the call before anything resets it. The number is also put back on any exit.
# Runs on the summit-air server; the app checkout is ~/summit-air/app. Copy this folder and
# evals/side-by-side/personas to one folder there (deploy step in docs/ai-process/briefs/side-by-side.md).
set -u
# The whole script is one function, so bash has read all of it before the first call: a copy
# replaced on disk mid-slate (as happened once) can no longer break a running slate.
main() {
  HERE="$(cd "$(dirname "$0")" && pwd)"
  # The persona scripts: personas/ beside this file when copied to the server, else the repo's.
  PERSONAS="$HERE/personas"
  [ -d "$PERSONAS" ] || PERSONAS="$(cd "$HERE/../../.." && pwd)/evals/side-by-side/personas"
  SLATE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  mkdir -p "$2/calls"
  OUT="$(cd "$2" && pwd)"
  NUMBER="$3"
  APP="$HOME/summit-air/app"
  BASE="https://45-77-105-247.sslip.io"
  export PATH="$HOME/.local/bin:$PATH"
  cd "$APP"
  py() { PYTHONPATH="$APP" uv run --env-file ../.env python "$@"; }

  # DB, MOCK, INBOUND, CODE and SETTLE for a world. CODE is the checkout whose reset_live_world matches
  # that world's schema. SETTLE is the wait after a reset: the live tool server
  # caches slots for SLOT_CACHE_SECONDS (60); the eval and server-led instances run with it off.
  world() {
    case "$1" in
      live)
        DB="$APP/data/tool_server.db"; MOCK="$APP/data/servicetitan_mock.db"
        INBOUND="$BASE/retell/inbound"; CODE="$APP"; SETTLE=65 ;;
      eval)
        DB="$HOME/summit-air/retell-data/eval_tools.db"; MOCK="$HOME/summit-air/retell-data/eval_mock.db"
        INBOUND="$BASE/eval/retell/inbound"; CODE="$APP"; SETTLE=5 ;;
      server-led)
        DB="$HOME/summit-air/server-led-data/tools.db"; MOCK="$HOME/summit-air/server-led-data/mock.db"
        INBOUND="https://server-led.45-77-105-247.sslip.io/retell/inbound"; CODE="$HOME/summit-air/server-led"
        SETTLE=5 ;;
      *) echo "unknown world $1"; return 1 ;;
    esac
  }

  py "$HERE/point.py" save "$NUMBER" "$OUT/number-before.json" || exit 1
  restore() { py "$HERE/point.py" restore "$NUMBER" "$OUT/number-before.json"; }
  trap restore EXIT

  live() {
    sqlite3 "$DB" "select count(*) from calls where ended_at is null and started_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 minutes')"
  }

  wait_quiet() {
    local n
    while true; do
      n="$(live)"
      [ "$n" = "0" ] && return 0
      echo "paused: $n live call(s) in $DB $(date -u +%T)"
      sleep 30
    done
  }

  i=0
  while read -r arm agent wname persona reset; do
    case "$arm" in ''|'#'*) continue ;; esac
    i=$((i + 1))
    tag="$(printf '%02d' "$i")-$arm-$persona"
    world "$wname" || continue
    wait_quiet
    if [ "$reset" = "reset" ]; then
      (cd "$CODE" && PYTHONPATH="$CODE" uv run --env-file "$HOME/summit-air/.env" python -m scripts.reset_live_world \
        --tool-db "$DB" --mock-db "$MOCK") > "$OUT/calls/$tag.reset.json" 2>&1 \
        || { echo "reset failed for $tag"; cat "$OUT/calls/$tag.reset.json"; continue; }
      sleep "$SETTLE"
      wait_quiet
    fi
    py "$HERE/point.py" arm "$NUMBER" "$agent" "$INBOUND" || { echo "could not point $NUMBER at $agent"; continue; }
    start_ms="$(date +%s%3N)"
    echo "=== $tag start $(date -u +%T)"
    timeout 420 env PYTHONPATH="$APP" uv run --env-file ../.env python "$HERE/run_persona.py" \
      "$PERSONAS/$persona.txt" --to "$NUMBER" --minutes 5.5 > "$OUT/calls/$tag.log" 2>&1
    end_ms="$(date +%s%3N)"
    restore
    sleep 15  # call-ended webhooks and the end-of-call writes land
    python3 "$HERE/rows.py" "$DB" "$start_ms" "$end_ms" > "$OUT/calls/$tag.rows.json" 2>&1
    room="$(grep -o 'testcall-[0-9a-f]*' "$OUT/calls/$tag.log" | head -1)"
    printf '{"n": %d, "arm": "%s", "agent_id": "%s", "world": "%s", "number": "%s", "persona": "%s", "reset": "%s", "start_ms": %s, "end_ms": %s, "room": "%s", "log": "calls/%s.log", "rows": "calls/%s.rows.json"}\n' \
      "$i" "$arm" "$agent" "$wname" "$NUMBER" "$persona" "$reset" "$start_ms" "$end_ms" "$room" "$tag" "$tag" >> "$OUT/manifest.jsonl"
    echo "=== $tag end $(date -u +%T)"
    sleep 20
  done < "$SLATE"
  echo "=== slate done $(date -u +%T)"
}
main "$@"
