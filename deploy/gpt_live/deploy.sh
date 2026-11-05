#!/bin/sh
# Deploy the GPT-Live phone service next to the shared services, without touching them: code to
# ~/summit-air/gpt-live, one systemd unit, and the /gpt-live/ route on the Retell lane's Caddy site
# (one site per host name, so the route lives in deploy/retell/Caddyfile).
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
  SITE=/etc/caddy/sites/summit-retell.caddy
  # Add the route to the live site file once, ahead of its catch-all, if a Retell deploy has not
  # already brought it.
  if ! sudo grep -q "handle /gpt-live/" "$SITE"; then
    sudo cp "$SITE" "$SITE.$(date +%Y%m%d%H%M%S).bak"
    sudo sed -i "0,/^\thandle {$/s//\thandle \/gpt-live\/* {\n\t\treverse_proxy 127.0.0.1:8104\n\t}\n\thandle {/" "$SITE"
    # Caddy reads the eval token from its own environment (deploy/retell/deploy.sh).
    sudo sh -c "set -a; . /home/jackson/summit-air/retell.env; caddy validate --config /etc/caddy/Caddyfile" >/dev/null
    sudo systemctl reload caddy
  fi
  sudo systemctl daemon-reload
  sudo systemctl enable -q summit-gpt-live
  sudo systemctl restart summit-gpt-live'
echo "deployed the GPT-Live phone service to $HOST"
