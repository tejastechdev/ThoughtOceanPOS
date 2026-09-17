#!/usr/bin/env sh
# Deploy the current branch on the server: pull code and images, restart the
# stack, update the custom addon(s). Run from anywhere; called by the GitHub
# Actions workflow over SSH, or by hand.
#
#   sh deploy/deploy.sh
#   UPDATE_MODULES=thoughtocean_pos,other sh deploy/deploy.sh
set -eu
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

if [ -f .env ]; then
  # shellcheck disable=SC1091
  . ./.env
fi
DB="${ODOO_DB:-thoughtocean}"
MODULES="${UPDATE_MODULES:-thoughtocean_pos}"

echo ">> Pulling code"
git pull --ff-only

echo ">> Pulling images and starting stack"
docker compose pull --quiet
docker compose up -d --remove-orphans

echo ">> Updating modules: ${MODULES} on database '${DB}'"
docker compose run --rm -T odoo \
  odoo -c /etc/odoo/odoo.conf -d "${DB}" -u "${MODULES}" --stop-after-init

echo ">> Restarting Odoo"
docker compose restart odoo

echo ">> Health check"
for _ in $(seq 1 60); do
  if docker compose exec -T odoo python3 -c \
       'import urllib.request; urllib.request.urlopen("http://localhost:8069/web/login", timeout=5)' \
       >/dev/null 2>&1; then
    echo ">> Deployed: https://${DOMAIN:-localhost}"
    exit 0
  fi
  sleep 2
done
echo "!! Odoo did not answer after restart; check: docker compose logs --tail=100 odoo" >&2
exit 1
