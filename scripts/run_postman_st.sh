#!/bin/sh
# Seed a throwaway world, start the mock on its own port, run the Postman collection with Newman,
# and stop the mock. Nothing is created in Postman's cloud. Exit code is Newman's.
set -eu

PORT="${ST_TEST_PORT:-8102}"
DIR="data/postman-run"
mkdir -p "$DIR"
rm -f "$DIR"/*

uv run python -m servicetitan_mock.seed --db "$DIR/world.db" > "$DIR/seed.log"

ST_MOCK_DB="$DIR/world.db" uv run uvicorn servicetitan_mock.app:create_app --factory \
  --port "$PORT" > "$DIR/server.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then break; fi
  sleep 0.2
done

NEWMAN="newman"
command -v newman > /dev/null || NEWMAN="npx -y newman"

$NEWMAN run postman/servicetitan-mock.postman_collection.json \
  -e postman/servicetitan-mock.postman_environment.json \
  --env-var "base_url=http://127.0.0.1:$PORT" --color off
