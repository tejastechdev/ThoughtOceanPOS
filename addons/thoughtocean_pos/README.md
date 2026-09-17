# ThoughtOcean Restaurant POS (`thoughtocean_pos`)

Odoo 18 Community module with ThoughtOcean's restaurant POS customisations.

## Depends on

- `point_of_sale` — core POS
- `pos_restaurant` — floors, tables, kitchen printing, bill splitting
- `l10n_au` — Australian chart of accounts and 10% GST taxes

## Uber Eats order intake

Orders placed on Uber Eats are created in the point of sale automatically.

**Flow**

1. Uber Eats calls `POST /uber_eats/webhook` (`orders.notification`). The
   request is authenticated with the `X-Uber-Signature` HMAC-SHA256 header
   (client secret); everything else is rejected with 401.
2. The event is stored as an `uber.eats.order` record (Point of Sale > Orders >
   Uber Eats Orders) and the HTTP reply is sent.
3. The order details are fetched from the Uber Eats API, a `pos.order` is
   created in the open session of the matching point of sale (take-away, note
   with the Uber display ID / customer / ready-by time / instructions), paid in
   full with an **Uber Eats** payment method (bank journal `UBER`, created on
   first use), the order is **accepted** on Uber with the POS receipt number, and
   the POS screen is refreshed.
4. `orders.cancel` cancels the POS order (if still open) and marks the record.

Failures are stored on the record (state *Error*) and retried every 2 minutes
for 30 minutes by the cron *Uber Eats: retry pending orders*. If no session is
open the order is denied on Uber (reason *store closed*) unless
*Deny orders when no session is open* is turned off.

**Item matching**: each Uber item is matched to a product that is available in
POS by Internal Reference = Uber `external_data`, then by the product's
*Uber Eats item ID* field, then by exact name; otherwise the point of sale's
fallback product ("Uber Eats item") is used with the Uber title on the line.
Prices come from Uber and are GST-inclusive, so lines are priced tax-inclusive:
each product tax is swapped for an automatically created tax-included twin
("10% GST (Uber Eats, tax incl.)", same rate). That keeps unit prices at whole
cents, which is what makes the POS screen, the server and Uber agree to the
cent (the POS front end rounds unit prices to cents before applying tax).
Modifiers that map to a product become their own line, others are folded into
the parent line's price and noted.

**Setup**

1. Point of Sale > Configuration > Settings > *Uber Eats*: enter the client ID
   and secret of your Uber Eats developer application, register the shown
   webhook URL in that application, and tick *Receive Uber Eats orders* on the
   point of sale (store ID from Uber Eats Manager if you have several stores).
2. Optionally set *Uber Eats item ID* on products, or make Internal References
   match the `external_data` of your Uber menu items.
3. Open a POS session. *Create test order* in Uber Eats Orders simulates an
   incoming order without contacting Uber.

Endpoints, scopes and paths are system parameters (`thoughtocean_pos.uber_*`)
in case Uber changes them.

## Layout

```
thoughtocean_pos/
├── controllers/main.py        # /uber_eats/webhook
├── models/
│   ├── uber_eats_api.py       # OAuth token, get/accept/deny order, signature check
│   ├── uber_eats_order.py     # intake pipeline, POS order creation, cron, test order
│   ├── pos_config.py          # per-POS settings, payment method / fallback product
│   ├── pos_order.py           # link back to Uber orders
│   ├── product_template.py    # uber_eats_id
│   └── res_config_settings.py # settings page fields
├── views/, data/, security/
└── tests/test_uber_eats.py    # intake, pricing, deny/cancel, webhook (mocked Uber API)
```

## Tests

```sh
docker compose run --rm odoo odoo -c /etc/odoo/odoo.conf -d test_thoughtocean \
  --db-filter "^test_thoughtocean$" \
  -i thoughtocean_pos --test-enable --test-tags /thoughtocean_pos --stop-after-init
```

The `--db-filter` override is needed because `odoo.conf` pins web requests to
the `thoughtocean` database; without it the webhook tests hit the wrong DB.

## Licence

MIT — see the repository `LICENSE`.
