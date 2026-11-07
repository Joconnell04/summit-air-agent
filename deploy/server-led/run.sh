#!/bin/sh
# Start one process of the server-led arm's test instance (see summit-server-led@.service, issue
# #171). Ports: 8130 tool server (SERVER_LED=1), 8131 its own mock ServiceTitan, 8132 the bridge
# its Retell agents call, 8133 the custom-LLM socket of the socket arm (issue #176). Nothing here
# talks to the live tool server (8100) or the live mock (8101).
# SUMMIT_CLOCK_OFFSET_H in ~/summit-air/server-led.env moves this world's clock (app/clock.py), so a
# night run can play a daytime call; the tool server and the mock read it together.
set -eu
export PATH="$HOME/.local/bin:$PATH"
DATA="$HOME/summit-air/server-led-data"
mkdir -p "$DATA"
case "$1" in
  mock)
    uv run python -m servicetitan_mock.seed --db "$DATA/mock.db" --if-stale
    SUMMIT_WORLD=server-led ST_MOCK_DB="$DATA/mock.db" ST_MOCK_ALLOW_RESET=1 ST_MOCK_RESET_OPEN=1 ST_MOCK_RESET_TOKEN="$SUMMIT_EVAL_TOKEN" \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8131 servicetitan_mock.app:create_app ;;
  tools)
    # Summaries off: they are written after the call and change nothing the caller hears.
    SUMMIT_WORLD=server-led SERVER_LED=1 FSM_BASE_URL=http://127.0.0.1:8131 TOOL_DB="$DATA/tools.db" \
      DEBUG_TOKEN="$SUMMIT_EVAL_TOKEN" SLOT_CACHE_SECONDS=0 TOOL_ALLOW_FORGET_CALLS=1 SUMMARIES= \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8130 app.main:create_app ;;
  bridge)
    TOOL_SERVER_URL=http://127.0.0.1:8130 RETELL_BRIDGE_DB="$DATA/bridge.db" \
      RETELL_NO_NOTE_AGENTS="${SERVER_LED_AGENTS:-}" RETELL_PRIVATE_NOTE_AGENTS= \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8132 voice.retell_flow.bridge:create_app ;;
  socket)
    # Retell's custom-LLM socket on this world: every server sentence spoken as written, the model
    # (config/models.yaml, MODEL_PROFILE or the default) writes the rest.
    TOOL_SERVER_URL=http://127.0.0.1:8130 RETELL_LLM_SERVER_LED=1 RETELL_LLM_TOOL_TIMEOUT_S=9 \
      RETELL_LLM_PROMPT=retell/socket/prompt.md RETELL_LLM_TOOLS=retell/socket/tools.yaml \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8133 voice.retell_llm.llm_socket:create_app ;;
  *) echo "unknown process $1" >&2; exit 2 ;;
esac
