#!/bin/sh
# Start one Retell-lane process (see summit-retell@.service). Ports: 8102 bridge; eval instance
# 8110 tool server, 8111 mock, 8112 bridge. The shared mock (8101) and tool server (8100) are
# started by deploy/summit-air/run.sh (the LiveKit lane, brief 03), which finds them with `pgrep -f "uvicorn app.main"` and
# "uvicorn servicetitan_mock"; the app comes last on the command lines below so those patterns
# never match an eval process and hide a stopped shared server.
set -eu
export PATH="$HOME/.local/bin:$PATH"
DATA="$HOME/summit-air/retell-data"
mkdir -p "$DATA"
case "$1" in
  bridge)
    TOOL_SERVER_URL=http://127.0.0.1:8100 RETELL_BRIDGE_DB="$DATA/bridge.db" \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8102 voice.retell.bridge:create_app ;;
  eval-mock)
    uv run python -m servicetitan_mock.seed --db "$DATA/eval_mock.db" --if-stale
    # Only this process allows POST /debug/reset (the eval harness rebuilds the seeded world with it
    # before each Retell scenario); the shared mock on 8101 never sets the flag.
    ST_MOCK_DB="$DATA/eval_mock.db" ST_MOCK_ALLOW_RESET=1 ST_MOCK_RESET_TOKEN="$SUMMIT_EVAL_TOKEN" \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8111 servicetitan_mock.app:create_app ;;
  eval-tools)
    FSM_BASE_URL=http://127.0.0.1:8111 TOOL_DB="$DATA/eval_tools.db" DEBUG_TOKEN="$SUMMIT_EVAL_TOKEN" \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8110 app.main:create_app ;;
  eval-bridge)
    TOOL_SERVER_URL=http://127.0.0.1:8110 RETELL_BRIDGE_DB="$DATA/eval_bridge.db" \
      exec uv run uvicorn --factory --host 127.0.0.1 --port 8112 voice.retell.bridge:create_app ;;
  *) echo "unknown process $1" >&2; exit 2 ;;
esac
