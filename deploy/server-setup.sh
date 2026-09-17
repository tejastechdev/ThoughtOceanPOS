#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 VM (tested target: Oracle Cloud
# "Always Free" Ampere A1). Installs Docker, opens ports 80/443, clones the repo,
# writes .env with random passwords and runs the initial Odoo setup.
#
# Usage (on the VM, as the default "ubuntu" user):
#   curl -fsSL https://raw.githubusercontent.com/tejastechdev/ThoughtOceanPOS/main/deploy/server-setup.sh \
#     | bash -s -- <domain> [git-url] [branch]
# e.g.
#   ... | bash -s -- maanspizza.duckdns.org
set -euo pipefail

DOMAIN="${1:?usage: server-setup.sh <domain> [git-url] [branch]}"
REPO="${2:-https://github.com/tejastechdev/ThoughtOceanPOS.git}"
BRANCH="${3:-main}"
APP_DIR="$HOME/ThoughtOceanPOS"

echo ">> 1/5 Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

echo ">> 2/5 Firewall (ports 80/443)"
# Oracle's Ubuntu images ship iptables rules that reject everything except SSH;
# insert allows at the top so they take effect before the REJECT rule.
for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
  # shellcheck disable=SC2086
  sudo iptables -C INPUT $rule -m state --state NEW -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 1 $rule -m state --state NEW -j ACCEPT
done
if ! dpkg -s iptables-persistent >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q iptables-persistent >/dev/null
fi
sudo netfilter-persistent save >/dev/null

echo ">> 3/5 Code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

echo ">> 4/5 Configuration (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
  sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" .env
  grep -q '^COMPOSE_FILE=' .env \
    || echo 'COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.prod.yml' >> .env
  echo "   wrote .env (random database password, DOMAIN=${DOMAIN}, production compose files)"
else
  echo "   .env already exists, left untouched"
fi

echo ">> 5/5 First deployment"
# The docker group membership only applies to new logins; "sg" applies it now.
sg docker -c "sh scripts/init.sh"

echo
echo "Done. Open https://${DOMAIN}  (admin / admin) and change the admin password first thing."
echo "Later deployments: sh deploy/deploy.sh   (or push to ${BRANCH} with the GitHub Action enabled)"
