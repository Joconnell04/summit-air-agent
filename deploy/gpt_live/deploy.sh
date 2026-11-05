#!/bin/sh
# Deploy the GPT-Live phone service next to the shared services, without touching them: code to
# ~/summit-air/gpt-live, one systemd unit, and a Caddy site on its own hostname
# (gpt-live.45-77-105-247.sslip.io), so a Retell deploy never drops it.
#   sh deploy/gpt_live/deploy.sh
# Needs OPENAI_API_KEY and OPENAI_WEBHOOK_SECRET in ~/summit-air/.env on the server. Deploy only
# when no call is live: restarting the service drops a call in progress.
set -eu
HOST="${HOST:-summit-air}"
ssh "$HOST" 'mkdir -p ~/summit-air/gpt-live'
rsync -az --delete --exclude .venv --exclude .git --exclude data --exclude '.env*' \
  --exclude __pycache__ --exclude .claude ./ "$HOST:summit-air/gpt-live/"
ssh "$HOST" 'set -e; cd ~/summit-air/gpt-live; ~/.local/bin/uv sync -q
  sudo install -m 644 deploy/gpt_live/summit-gpt-live.service /etc/systemd/system/
  sudo install -m 644 deploy/gpt_live/Caddyfile /etc/caddy/sites/summit-gpt-live.caddy
  # Caddy reads the Retell eval token from its own environment (deploy/retell/deploy.sh).
  sudo sh -c "set -a; . /home/jackson/summit-air/retell.env; caddy validate --config /etc/caddy/Caddyfile" >/dev/null
  sudo systemctl reload caddy
  sudo systemctl daemon-reload
  sudo systemctl enable -q summit-gpt-live
  sudo systemctl restart summit-gpt-live'
echo "deployed the GPT-Live phone service to $HOST"
