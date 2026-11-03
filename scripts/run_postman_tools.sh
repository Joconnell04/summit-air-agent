#!/bin/sh
# Seed a throwaway world, start the mock ServiceTitan and the tool server on their own ports, run
# the tool server's Postman collection with Newman, and stop both. Nothing is created in Postman's
# cloud. Exit code is Newman's.
set -eu

ST_PORT="${ST_TEST_PORT:-8104}"
PORT="${TOOL_TEST_PORT:-8103}"
DIR="data/postman-tools"
TOKEN="postman-debug-token"
OWNER="postman-owner-token"
mkdir -p "$DIR"
rm -f "$DIR"/*

uv run python -m servicetitan_mock.seed --db "$DIR/world.db" > "$DIR/seed.log"

ST_MOCK_DB="$DIR/world.db" uv run uvicorn servicetitan_mock.app:create_app --factory \
  --port "$ST_PORT" > "$DIR/mock.log" 2>&1 &
MOCK=$!
TOOL_DB="$DIR/tool.db" FSM_BASE_URL="http://127.0.0.1:$ST_PORT" DEBUG_TOKEN="$TOKEN" \
  OWNER_TOKEN="$OWNER" JOBS_INTERVAL_SECONDS=0 uv run uvicorn app.main:create_app --factory \
  --port "$PORT" > "$DIR/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER $MOCK 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$ST_PORT/health" > /dev/null \
    && curl -sf "http://127.0.0.1:$PORT/healthz" > /dev/null; then break; fi
  sleep 0.2
done

NEWMAN="newman"
command -v newman > /dev/null || NEWMAN="npx -y newman"

$NEWMAN run postman/tool-server.postman_collection.json \
  -e postman/tool-server.postman_environment.json \
  --env-var "base_url=http://127.0.0.1:$PORT" --env-var "st_url=http://127.0.0.1:$ST_PORT" \
  --env-var "debug_token=$TOKEN" --env-var "owner_token=$OWNER" --color off
