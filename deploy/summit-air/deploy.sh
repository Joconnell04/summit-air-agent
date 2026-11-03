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
  ~/.local/bin/uv run --env-file ../.env python -m livekit.agents download-files >/dev/null 2>&1
  # Bracketed so the pattern never matches this shell; ports so the Retell stack is left alone.
  pkill -u "$(id -u)" -f "[a]pp.main.*--port 8100|[v]oice.livekit.worker start|[v]oice.livekit.outbound" || true
  sleep 2
  sh deploy/summit-air/run.sh
  # Replace only our own lines; anything else in the crontab stays.
  { crontab -l 2>/dev/null | grep -v "summit-air/app/deploy" | grep -v "^# Installed by deploy/summit-air"
    cat deploy/summit-air/crontab; } | crontab -'
echo "deployed to $HOST"
