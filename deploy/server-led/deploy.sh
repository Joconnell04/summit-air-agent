#!/bin/sh
# Deploy the server-led arm's test instance (issue #171) next to the live services, without
# touching them: code to ~/summit-air/server-led, four systemd units, and a Caddy site on its own
# hostname. Caddy is reloaded, never restarted, and gets no new environment: the site reads the
# eval token Caddy already has (deploy/retell/deploy.sh).
#   sh deploy/server-led/deploy.sh
# ~/summit-air/server-led.env (optional) sets SERVER_LED_AGENTS, the arm's agent ids, comma
# separated, which get no mid-call notes, as on Jackson's agent.
set -eu
HOST="${HOST:-summit-air}"
ssh "$HOST" 'mkdir -p ~/summit-air/server-led ~/summit-air/server-led-data
  test -f ~/summit-air/retell.env || { echo "needs ~/summit-air/retell.env (deploy/retell)" >&2; exit 1; }'
rsync -az --delete --exclude .venv --exclude .git --exclude data --exclude '.env*' \
  --exclude __pycache__ --exclude .claude ./ "$HOST:summit-air/server-led/"
ssh "$HOST" 'set -e; cd ~/summit-air/server-led; ~/.local/bin/uv sync -q
  sudo install -m 644 deploy/server-led/summit-server-led@.service /etc/systemd/system/
  sudo install -m 644 deploy/server-led/Caddyfile /etc/caddy/sites/summit-server-led.caddy
  sudo systemctl daemon-reload
  for p in mock tools bridge socket; do
    sudo systemctl enable -q "summit-server-led@$p"; sudo systemctl restart "summit-server-led@$p"
  done
  # A site that fails to parse would stop the reload for every site: take it out instead.
  # Validated with the eval token Caddy runs with, which the sites read.
  if ! sudo env "$(grep ^SUMMIT_EVAL_TOKEN= ~/summit-air/retell.env)" \
      caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    sudo rm /etc/caddy/sites/summit-server-led.caddy
    echo "the server-led Caddy site did not validate; removed it" >&2; exit 1
  fi
  sudo systemctl reload caddy'
echo "deployed the server-led arm to $HOST"
