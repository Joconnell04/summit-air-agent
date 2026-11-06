#!/usr/bin/env bash
# Scripted phone calls to the socket arm on the (646) line, one at a time (issue #176). A copy of
# the side-by-side runner (voice/retell/side_by_side/run_slate.sh on joco/side-by-side-014a4c) for
# one arm and one world, with the line's owner protected:
#
#   setsid -f bash run_slate.sh <slate> <out_dir> > <out_dir>/runner.log 2>&1 < /dev/null
#
# Each slate line is "<persona_file> <reset|keep>". Per line: wait until no call is live on the
# line (Retell) or in the server-led world, reset the world if asked, point the line at the socket
# agent, dial the persona, put the line back on Jackson's agent, then dump the world's rows for
# the call. The line goes back to Jackson's agent on any exit, error or signal included.
# Runs on the summit-air server from the arm's checkout (~/summit-air/server-led), with
# run_persona.py and rows.py from the side-by-side folder in ~/summit-air/socket-runs/tools (outside
# the checkout, which deploy.sh replaces).
set -u
main() {
  TOOLS="${SOCKET_TOOLS:-$HOME/summit-air/socket-runs/tools}"
  for f in run_persona.py rows.py; do
    [ -f "$TOOLS/$f" ] || { echo "missing $TOOLS/$f"; exit 2; }
  done
  SLATE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  mkdir -p "$2/calls"
  OUT="$(cd "$2" && pwd)"
  NUMBER="+16466240418"
  CODE="$HOME/summit-air/server-led"
  DATA="$HOME/summit-air/server-led-data"
  export PATH="$HOME/.local/bin:$PATH"
  cd "$CODE"
  py() { PYTHONPATH="$CODE" uv run --env-file "$HOME/summit-air/.env" python "$@"; }

  restore() { py retell/arms/socket/line.py restore; }
  trap restore EXIT
  trap 'exit 130' INT TERM

  quiet() {
    local n
    while true; do
      n="$(sqlite3 "$DATA/tools.db" "select count(*) from calls where ended_at is null and started_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 minutes')")"
      if [ "$n" = "0" ] && py retell/arms/socket/line.py busy; then
        return 0
      fi
      echo "paused: line or world busy ($n open in the world) $(date -u +%T)"
      sleep 30
    done
  }

  i=0
  while read -r persona reset; do
    case "$persona" in ''|'#'*) continue ;; esac
    i=$((i + 1))
    tag="$(printf '%02d' "$i")-$(basename "$persona" .txt)"
    quiet
    if [ "$reset" = "reset" ]; then
      py -m scripts.reset_live_world --tool-db "$DATA/tools.db" --mock-db "$DATA/mock.db" \
        > "$OUT/calls/$tag.reset.json" 2>&1 || { echo "reset failed for $tag"; continue; }
      sleep 5
      quiet
    fi
    before="$(sqlite3 "$DATA/tools.db" 'select coalesce(max(rowid), 0) from calls')"
    py retell/arms/socket/line.py arm || { echo "could not point the line for $tag"; continue; }
    start_ms="$(date +%s%3N)"
    echo "=== $tag start $(date -u +%T)"
    timeout 420 env PYTHONPATH="$CODE" uv run --env-file "$HOME/summit-air/.env" python \
      "$TOOLS/run_persona.py" "$persona" --to "$NUMBER" --minutes 5.5 > "$OUT/calls/$tag.log" 2>&1 &
    caller=$!
    # A second call on the line while ours runs is someone else, most likely Jackson: the line
    # goes back to his agent at once and the slate stops.
    ( while kill -0 "$caller" 2>/dev/null; do
        sleep 10
        if [ "$(py retell/arms/socket/line.py count 2>/dev/null)" -gt 1 ] 2>/dev/null; then
          restore; echo "second call on the line: restored, stopping" > "$OUT/STOPPED"
          kill "$caller"; break
        fi
      done ) &
    watcher=$!
    wait "$caller"
    kill "$watcher" 2>/dev/null
    end_ms="$(date +%s%3N)"
    restore
    sleep 15  # the socket closes the call on the tool server
    python3 "$TOOLS/rows.py" "$DATA/tools.db" "$start_ms" "$end_ms" "$before" > "$OUT/calls/$tag.rows.json" 2>&1
    printf '{"n": %d, "arm": "socket", "agent_id": "%s", "world": "server-led", "number": "%s", "persona": "%s", "reset": "%s", "start_ms": %s, "end_ms": %s, "log": "calls/%s.log", "rows": "calls/%s.rows.json"}\n' \
      "$i" "${SOCKET_AGENT:-}" "$NUMBER" "$(basename "$persona" .txt)" "$reset" "$start_ms" "$end_ms" "$tag" "$tag" >> "$OUT/manifest.jsonl"
    echo "=== $tag end $(date -u +%T)"
    [ -f "$OUT/STOPPED" ] && { echo "=== stopped: $(cat "$OUT/STOPPED")"; exit 1; }
    sleep 10
  done < "$SLATE"
  echo "=== slate done $(date -u +%T)"
}
main "$@"
