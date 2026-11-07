#!/bin/sh
# Makes committed main live in one step: the tool server and its config, the voice socket, and
# the Retell agent's listening and voice settings (copied from Jackson's agent by push.py).
#
#     make live
#
# It stops before changing anything when the checkout isn't main, has uncommitted changes, or a
# call is on the line, so a deploy never lands mid-call.
set -eu
cd "$(dirname "$0")/../.."

[ "$(git branch --show-current)" = main ] || { echo "stopped: switch to main first (git switch main)"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "stopped: commit or stash your changes first"; exit 1; }
git pull -q --ff-only origin main
git push -q origin main

uv run -q --env-file .env python retell/socket/line.py busy || { echo "stopped: a call is on the line, try again after it ends"; exit 1; }

sh deploy/summit-air/deploy.sh
sh deploy/retell/deploy.sh
# --live updates the production agent and leaves the numbers alone; without it, push.py points
# the demo number at the test agent.
uv run -q --env-file .env python retell/socket/push.py --live

ssh "${HOST:-summit-air}" 'curl -s localhost:8100/healthz' | grep -q '"ok":true' || { echo "deployed, but the health check failed"; exit 1; }
echo "live: $(git log --oneline -1)"
