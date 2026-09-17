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
- Git Bash or any POSIX shell (Windows: Docker Desktop with the WSL 2 backend)

## Setup

```sh
git clone https://github.com/tejastechdev/ThoughtOceanPOS.git
cd ThoughtOceanPOS
cp .env.example .env          # optional; init.sh does this if missing
sh scripts/init.sh
```

`scripts/init.sh`:

1. starts Postgres and Odoo via `docker compose up -d`
2. creates the `thoughtocean` database (no demo data) and sets the company
   name and country from `ODOO_COMPANY` / `ODOO_COUNTRY` in `.env`
   (defaults: Maan's Pizza Shop, AU), so the matching chart of accounts,
   currency and taxes load from the start
3. installs `pos_restaurant`, `l10n_au` (Australian chart of accounts + 10% GST)
   and the custom `thoughtocean_pos` addon
4. restarts Odoo and waits until <http://localhost:8069> answers

Then open <http://localhost:8069/web?db=thoughtocean> and log in with
`admin` / `admin`.

### Installing the custom addon

```sh
docker compose exec odoo odoo -c /etc/odoo/odoo.conf -d thoughtocean -i thoughtocean_pos --stop-after-init
docker compose restart odoo
```

or from Apps in the UI (developer mode → Update Apps List → "ThoughtOcean").

## Uber Eats orders

`thoughtocean_pos` receives Uber Eats orders: Uber calls the webhook
`https://<your-domain>/uber_eats/webhook`, the order is created as a paid,
take-away POS order in the open session, accepted on Uber and shown on the POS
screen. Configure it under Point of Sale → Configuration → Settings → Uber Eats
(client ID/secret from your Uber Eats developer application, then enable it on
the point of sale) and try **Create test order** under Point of Sale → Orders →
Uber Eats Orders. Details and item-matching rules:
[addons/thoughtocean_pos/README.md](addons/thoughtocean_pos/README.md).

Getting API access: Uber Eats integrations go through the
[Uber Developer portal](https://developer.uber.com) (create an app, request the
Eats Marketplace `eats.order` / `eats.store` scopes, and link your store); Uber
approves POS integrations per merchant, so start that request early.

## Deploy to a server (Oracle Cloud free tier)

Everything under [deploy/](deploy/) turns the same stack into a public HTTPS
site: Caddy in front (automatic Let's Encrypt certificates), Odoo in
multi-worker mode and not exposed directly, database manager disabled.
Tested target: an Oracle Cloud "Always Free" Ampere A1 VM, but any Ubuntu
22.04/24.04 box with a public IP works the same way.

### 1. Create the VM

In the Oracle Cloud console: Compute → Instances → Create.

- Image: Ubuntu 24.04 (aarch64). Shape: `VM.Standard.A1.Flex`, 2 OCPU / 6 GB
  (within the free allowance; retry later or in another availability domain if
  you get "Out of capacity").
- Add your SSH public key. Note the public IP once it is running.
- Networking → the instance's subnet → Security list → add two ingress rules:
  source `0.0.0.0/0`, TCP, destination ports `80` and `443`.

### 2. Point a hostname at it

Free option: [DuckDNS](https://www.duckdns.org) — create e.g.
`maanspizza.duckdns.org` and set it to the VM's public IP. Any DNS name you
own works too. Caddy needs the name to resolve before it can get a certificate.

### 3. Run the setup script on the VM

```sh
ssh ubuntu@<public-ip>
curl -fsSL https://raw.githubusercontent.com/tejastechdev/ThoughtOceanPOS/main/deploy/server-setup.sh \
  | bash -s -- maanspizza.duckdns.org
```

[deploy/server-setup.sh](deploy/server-setup.sh) installs Docker, opens ports
80/443 in the VM's firewall, clones this repo to `~/ThoughtOceanPOS`, writes
`.env` (random database password, your domain, production compose files) and
runs `scripts/init.sh`. About 5 minutes. Then open `https://<your-domain>`,
log in with `admin` / `admin` and **change the admin password immediately**.

### 4. Automatic deploys from GitHub (optional)

Every push to `main` runs the **CI / Deploy** workflow
([.github/workflows/deploy.yml](.github/workflows/deploy.yml)): it validates
shell scripts, addon Python, compose files and the Caddyfile, then SSHes into
the VM and runs [deploy/deploy.sh](deploy/deploy.sh) (git pull, pull images,
restart, update `thoughtocean_pos`, health check). Pull requests only run the
validation.

To enable the deploy step:

1. Create a dedicated key pair: `ssh-keygen -t ed25519 -f deploy_key -N ""`,
   and append `deploy_key.pub` to `~/.ssh/authorized_keys` on the VM.
2. In the GitHub repo, Settings → Secrets and variables → Actions:
   - Secrets: `DEPLOY_HOST` (public IP or domain), `DEPLOY_USER` (`ubuntu`),
     `DEPLOY_SSH_KEY` (contents of the private `deploy_key`).
   - Variables: `DEPLOY_ENABLED` = `true`.

Manual deploy at any time: `ssh ubuntu@<ip> 'cd ~/ThoughtOceanPOS && sh deploy/deploy.sh'`.

### Production notes

- Configuration lives in `.env` on the server (never committed).
  `COMPOSE_FILE` there makes every `docker compose` command include
  [deploy/docker-compose.prod.yml](deploy/docker-compose.prod.yml).
- Odoo uses [deploy/odoo.prod.conf](deploy/odoo.prod.conf) (`proxy_mode`,
  2 workers, `list_db = False`, only the `thoughtocean` database exposed).
- Backups: `docker compose exec db pg_dump -U odoo thoughtocean | gzip > backup.sql.gz`
  plus the `odoo-data` volume (filestore). Not automated yet.
- The free VM is reclaimed by Oracle if idle for a long time; a POS in use
  is not idle.

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
