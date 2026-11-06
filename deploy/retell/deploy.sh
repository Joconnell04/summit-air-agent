#!/bin/sh
# Deploy the Retell lane next to the shared services (deploy/summit-air/deploy.sh), without
# touching them: code to ~/summit-air/retell, five systemd units, and a Caddy site for HTTPS.
#   sh deploy/retell/deploy.sh
# Needs ~/summit-air/.env on the server (RETELL_API_KEY); creates ~/summit-air/retell.env with a
# fresh eval token the first time.
set -eu
HOST="${HOST:-summit-air}"
ssh "$HOST" 'mkdir -p ~/summit-air/retell ~/summit-air/retell-data
  test -x ~/.local/bin/uv || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  test -f ~/summit-air/retell.env || { umask 077; echo "SUMMIT_EVAL_TOKEN=$(openssl rand -hex 24)" > ~/summit-air/retell.env; }'
rsync -az --delete --exclude .venv --exclude .git --exclude data --exclude '.env*' \
  --exclude __pycache__ --exclude .claude ./ "$HOST:summit-air/retell/"
ssh "$HOST" 'set -e; cd ~/summit-air/retell; ~/.local/bin/uv sync -q
  sudo install -m 644 deploy/retell/summit-retell@.service /etc/systemd/system/
  sudo mkdir -p /etc/caddy/sites
  sudo install -m 644 deploy/retell/Caddyfile /etc/caddy/sites/summit-retell.caddy
  # Add the import line once, keeping whatever else the Caddyfile serves.
  if ! grep -q "^import /etc/caddy/sites/" /etc/caddy/Caddyfile; then
    sudo cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.$(date +%Y%m%d%H%M%S).bak"
    printf "\nimport /etc/caddy/sites/*.caddy\n" | sudo tee -a /etc/caddy/Caddyfile >/dev/null
  fi
  # Caddy reads the eval token from its own environment.
  DROPIN=/etc/systemd/system/caddy.service.d/summit-retell.conf
  had_dropin=no; test -f "$DROPIN" && had_dropin=yes
  sudo mkdir -p /etc/systemd/system/caddy.service.d
  printf "[Service]\nEnvironmentFile=/home/jackson/summit-air/retell.env\n" | \
    sudo tee "$DROPIN" >/dev/null
  sudo systemctl daemon-reload
  for p in bridge eval-mock eval-tools eval-bridge eval-llm llm; do
    sudo systemctl enable -q "summit-retell@$p"; sudo systemctl restart "summit-retell@$p"
  done
  # A reload keeps the owner view and the other sites up; a restart is needed only the first time
  # the drop-in above gives Caddy the eval token.
  if [ "$had_dropin" = yes ]; then
    sudo systemctl reload caddy
  else
    sudo systemctl restart caddy
  fi'
echo "deployed the Retell lane to $HOST"
