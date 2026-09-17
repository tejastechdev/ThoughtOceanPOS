#!/usr/bin/env sh
# Bring up the Odoo 18 stack and initialise the "thoughtocean" database with
# the restaurant POS and Australian (GST) localisation modules.
#
# Usage: scripts/init.sh            (from the repository root)
#        ODOO_DB=other scripts/init.sh
set -eu

# Git Bash on Windows rewrites POSIX paths such as /etc/odoo/odoo.conf into
# Windows paths before passing them to docker; disable that (no-op elsewhere).
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

DB="${ODOO_DB:-thoughtocean}"
MODULES="pos_restaurant,l10n_au"

if [ ! -f .env ] && [ -f .env.example ]; then
  echo ">> No .env found, copying .env.example"
  cp .env.example .env
fi

echo ">> Starting stack"
docker compose up -d

echo ">> Waiting for Postgres"
for _ in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U "${POSTGRES_USER:-odoo}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if docker compose exec -T db psql -U "${POSTGRES_USER:-odoo}" -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='${DB}'" | grep -q 1; then
  echo ">> Database '${DB}' already exists; installing/updating ${MODULES}"
else
  echo ">> Creating database '${DB}' and installing ${MODULES}"
fi

# Odoo creates the database if it does not exist. Run as a one-off container so
# it does not fight the long-running server for the same DB during init.
docker compose run --rm -T odoo \
  odoo -c /etc/odoo/odoo.conf \
       -d "${DB}" \
       -i "${MODULES}" \
       --load-language=en_AU \
       --without-demo=all \
       --stop-after-init

echo ">> Restarting Odoo so it picks up the new database"
docker compose restart odoo

echo ">> Waiting for Odoo on http://localhost:${ODOO_PORT:-8069}"
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${ODOO_PORT:-8069}/web/login" >/dev/null 2>&1; then
    echo ">> Odoo is up: http://localhost:${ODOO_PORT:-8069}/web?db=${DB}  (admin / admin)"
    exit 0
  fi
  sleep 2
done

echo "!! Odoo did not respond in time; check: docker compose logs -f odoo" >&2
exit 1
