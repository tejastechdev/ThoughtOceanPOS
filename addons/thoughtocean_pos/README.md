# ThoughtOcean Restaurant POS (`thoughtocean_pos`)

Skeleton Odoo 18 Community module for ThoughtOcean's restaurant POS customisations.

## Depends on

- `point_of_sale` — core POS
- `pos_restaurant` — floors, tables, kitchen printing, bill splitting
- `l10n_au` — Australian chart of accounts and 10% GST taxes

## Layout

```
thoughtocean_pos/
├── __init__.py       # import models/, controllers/ etc. as they are added
├── __manifest__.py
└── README.md
```

Add `models/`, `views/`, `static/src/` (POS JS/OWL assets) and `security/` as
features are built out; register data files in `__manifest__.py` under `data`
and POS frontend assets under `assets["point_of_sale._assets_pos"]`.

## Install

From the repo root, with the stack running:

```sh
docker compose exec odoo odoo -c /etc/odoo/odoo.conf -d thoughtocean -i thoughtocean_pos --stop-after-init
docker compose restart odoo
```

or via Apps in the Odoo UI (enable developer mode, Update Apps List, search "ThoughtOcean").

## Licence

MIT — see the repository `LICENSE`.
