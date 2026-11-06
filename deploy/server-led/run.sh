#!/bin/sh
# Start one process of the server-led arm's test instance (see summit-server-led@.service, issue
# #171). Ports: 8130 tool server (SERVER_LED=1), 8131 its own mock ServiceTitan, 8132 the bridge
# its Retell agents call. Nothing here talks to the live tool server (8100) or the live mock (8101).
set -eu
export PATH="$HOME/.local/bin:$PATH"
DATA="$HOME/summit-air/server-led-data"
mkdir -p "$DATA"
case "$1" in
  mock)
    uv run python -m servicetitan_mock.seed --db "$DATA/mock.db" --if-stale
    ST_MOCK_DB="$DATA/mock.db" ST_MOCK_ALLOW_RESET=1 ST_MOCK_RESET_TOKEN="$SUMMIT_EVAL_TOKEN" \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8131 servicetitan_mock.app:create_app ;;
  tools)
    # Summaries off: they are written after the call and change nothing the caller hears.
    SERVER_LED=1 FSM_BASE_URL=http://127.0.0.1:8131 TOOL_DB="$DATA/tools.db" \
      DEBUG_TOKEN="$SUMMIT_EVAL_TOKEN" SLOT_CACHE_SECONDS=0 TOOL_ALLOW_FORGET_CALLS=1 SUMMARIES= \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8130 app.main:create_app ;;
  bridge)
    TOOL_SERVER_URL=http://127.0.0.1:8130 RETELL_BRIDGE_DB="$DATA/bridge.db" \
      RETELL_NO_NOTE_AGENTS="${SERVER_LED_AGENTS:-}" RETELL_PRIVATE_NOTE_AGENTS= \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8132 voice.retell_flow.bridge:create_app ;;
  *) echo "unknown process $1" >&2; exit 2 ;;
esac
