#!/bin/sh
# Serves the seeded demo day for the captures: the mock ServiceTitan on 8121 over the seeded world,
# and the tool server on 8120 reading it, both frozen at the seed's "now" (5:20 PM). Local only.
# Seed first with: uv run python -m scripts.seed_owner_day --dir data/owner-demo
set -eu

DEMO=data/owner-demo
NOW=$(cat "$DEMO/now")
ST_MOCK_DB=$DEMO/world.db ST_MOCK_NOW=$NOW \
    uv run uvicorn servicetitan_mock.app:create_app --factory --port 8121 &
FSM_BASE_URL=http://127.0.0.1:8121 TOOL_DB=$DEMO/tool.db JOBS_INTERVAL_SECONDS=0 FIXED_NOW=$NOW \
    uv run uvicorn app.main:create_app --factory --port 8120 &
wait
