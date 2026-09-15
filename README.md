# ThoughtOcean POS

Odoo 18 Community restaurant point-of-sale for ThoughtOcean, with the
Australian localisation (GST) and a custom addon (`thoughtocean_pos`).

## Stack

| Service  | Image         | Port |
|----------|---------------|------|
| odoo     | `odoo:18`     | 8069 (web), 8072 (websocket/longpolling) |
| db       | `postgres:16` | internal |

Named volumes `odoo-data` (filestore/sessions) and `db-data` persist across
restarts. `./addons` is mounted at `/mnt/extra-addons` and is on `addons_path`
(see [odoo.conf](odoo.conf)).

## Prerequisites

- Docker Engine / Docker Desktop with the `docker compose` plugin
- `curl` (used by the init script's readiness check)

## Setup

```sh
git clone https://github.com/tejastechdev/ThoughtOceanPOS.git
cd ThoughtOceanPOS
cp .env.example .env          # optional; init.sh does this if missing
sh scripts/init.sh
```

`scripts/init.sh`:

1. starts Postgres and Odoo via `docker compose up -d`
2. creates the `thoughtocean` database (no demo data)
3. installs `pos_restaurant` and `l10n_au` (Australian chart of accounts + 10% GST)
4. restarts Odoo and waits until <http://localhost:8069> answers

Then open <http://localhost:8069/web?db=thoughtocean> and log in with
`admin` / `admin`.

### Installing the custom addon

```sh
docker compose exec odoo odoo -c /etc/odoo/odoo.conf -d thoughtocean -i thoughtocean_pos --stop-after-init
docker compose restart odoo
```

or from Apps in the UI (developer mode → Update Apps List → "ThoughtOcean").

## Day-to-day

```sh
docker compose up -d              # start
docker compose logs -f odoo       # tail logs
docker compose restart odoo       # after editing Python in ./addons
docker compose down               # stop (data kept)
docker compose down -v            # stop and wipe database + filestore
```

Update a module after code changes:

```sh
docker compose exec odoo odoo -c /etc/odoo/odoo.conf -d thoughtocean -u thoughtocean_pos --stop-after-init
```

## Repository layout

```
.
├── addons/
│   └── thoughtocean_pos/     # custom addon (MIT)
├── odoo.conf                 # mounted at /etc/odoo/odoo.conf
├── scripts/
│   └── init.sh               # bring up + initialise database
├── docker-compose.yml
├── .env.example
└── LICENSE                   # MIT (covers ./addons; Odoo itself is LGPL-3)
```

## Licence

Code in this repository (including `addons/`) is MIT licensed — see
[LICENSE](LICENSE). Odoo Community itself is LGPL-3.
