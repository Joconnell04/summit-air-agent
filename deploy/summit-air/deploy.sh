#!/bin/sh
# Copy this checkout to the summit-air server, install, restart, and install the crontab.
#   sh deploy/summit-air/deploy.sh [path to .env]
# The .env goes to ~/summit-air/.env (mode 600); the code to ~/summit-air/app.
set -eu
ENV_FILE="${1:-.env}"
HOST="${HOST:-summit-air}"
ssh "$HOST" 'mkdir -p ~/summit-air/app ~/summit-air/logs
  test -x ~/.local/bin/uv || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null'
rsync -az --delete --exclude .venv --exclude .git --exclude data --exclude '.env*' \
  --exclude __pycache__ --exclude .claude ./ "$HOST:summit-air/app/"
scp -q "$ENV_FILE" "$HOST:summit-air/.env"
ssh "$HOST" 'set -e; chmod 600 ~/summit-air/.env; cd ~/summit-air/app
  ~/.local/bin/uv sync -q
  ~/.local/bin/uv run --env-file ../.env python -m voice.livekit.worker download-files >/dev/null 2>&1
  pkill -u "$(id -u)" -f "uvicorn app.main|voice.livekit.worker" || true
  sleep 2
  sh deploy/summit-air/run.sh
  crontab deploy/summit-air/crontab'
echo "deployed to $HOST"
