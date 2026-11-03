#!/bin/sh
# Deploy the Retell lane next to the shared services (deploy/summit-air/deploy.sh), without
# touching them: code to ~/summit-air/retell, four systemd units, and a Caddy site for HTTPS.
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
  grep -q "^import /etc/caddy/sites/" /etc/caddy/Caddyfile || \
    printf "import /etc/caddy/sites/*.caddy\n" | sudo tee /etc/caddy/Caddyfile >/dev/null
  # Caddy reads the eval token from its own environment.
  sudo mkdir -p /etc/systemd/system/caddy.service.d
  printf "[Service]\nEnvironmentFile=/home/jackson/summit-air/retell.env\n" | \
    sudo tee /etc/systemd/system/caddy.service.d/summit-retell.conf >/dev/null
  sudo systemctl daemon-reload
  for p in bridge eval-mock eval-tools eval-bridge; do
    sudo systemctl enable -q "summit-retell@$p"; sudo systemctl restart "summit-retell@$p"
  done
  sudo systemctl restart caddy'
echo "deployed the Retell lane to $HOST"
