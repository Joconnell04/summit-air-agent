#!/bin/sh
# Deploy the Deepgram entry next to the shared services without touching them: code to
# ~/summit-air/deepgram, one systemd unit, and its own Caddy site.
#   sh deploy/deepgram_agent/deploy.sh
# Needs ~/summit-air/.env on the server (DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, TWILIO_*).
set -eu
HOST="${HOST:-summit-air}"
ssh "$HOST" 'mkdir -p ~/summit-air/deepgram'
rsync -az --delete --exclude .venv --exclude .git --exclude data --exclude '.env*' \
  --exclude __pycache__ --exclude .claude ./ "$HOST:summit-air/deepgram/"
ssh "$HOST" 'set -e; cd ~/summit-air/deepgram; ~/.local/bin/uv sync -q
  sudo install -m 644 deploy/deepgram_agent/summit-deepgram.service /etc/systemd/system/
  sudo install -m 644 deploy/deepgram_agent/Caddyfile /etc/caddy/sites/summit-deepgram.caddy
  sudo systemctl daemon-reload
  sudo systemctl enable -q summit-deepgram; sudo systemctl restart summit-deepgram
  sudo systemctl reload caddy'
echo "deployed the Deepgram entry to $HOST"
