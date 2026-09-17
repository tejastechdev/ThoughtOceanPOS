"""Uber Eats order intake.

Flow: webhook (orders.notification) -> uber.eats.order record (state received)
-> _process(): fetch order details from Uber, create a pos.order in the open
session of the matching pos.config, record an "Uber Eats" payment, accept the
order on Uber, notify the POS screen -> state accepted.

Failures are recorded on the record (state error) and retried by a cron for a
short while; Uber itself auto-cancels orders not accepted within its window.
"""
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ORDER_TYPES = [
    ("DELIVERY_BY_UBER", "Delivery by Uber"),
    ("DELIVERY_BY_RESTAURANT", "Delivery by restaurant"),
    ("PICKUP", "Pickup"),
    ("DINE_IN", "Dine in"),
    ("unknown", "Unknown"),
]
ACTIVE_EVENTS = ("orders.notification", "orders.release", "orders.scheduled.notification")
CANCEL_EVENTS = ("orders.cancel",)
MAX_ATTEMPTS = 8
RETRY_WINDOW = timedelta(minutes=30)


def _money(value):
    """Uber sends amounts as integers in minor units ({"amount": 1250} = 12.50)."""
    if isinstance(value, dict):
        value = value.get("amount", 0)
    return (value or 0) / 100.0


def _iso_to_datetime(value):
    if not value:
        return False
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class UberEatsOrder(models.Model):
    _name = "uber.eats.order"
    _description = "Uber Eats order"
    _order = "create_date desc, id desc"
    _rec_name = "display_id"

    uber_order_id = fields.Char(string="Uber order ID", required=True, index=True, readonly=True)
    display_id = fields.Char(string="Uber display ID", readonly=True)
    store_id = fields.Char(string="Uber store ID", readonly=True)
    event_type = fields.Char(readonly=True)
    state = fields.Selection([
        ("received", "Received"),
        ("accepted", "Accepted"),
        ("denied", "Denied"),
        ("cancelled", "Cancelled"),
        ("error", "Error"),
    ], default="received", required=True, index=True, readonly=True)
    pos_config_id = fields.Many2one("pos.config", string="Point of Sale", readonly=True)
    company_id = fields.Many2one(related="pos_config_id.company_id", store=True)
    pos_order_id = fields.Many2one("pos.order", string="POS order", readonly=True)
    order_type = fields.Selection(ORDER_TYPES, default="unknown", readonly=True)
    eater_name = fields.Char(string="Customer", readonly=True)
    placed_at = fields.Datetime(readonly=True)
    ready_at = fields.Datetime(string="Ready for pickup at", readonly=True)
    amount_uber = fields.Float(string="Items total (Uber)", readonly=True, digits="Product Price")
    amount_pos = fields.Float(string="Order total (POS)", readonly=True, digits="Product Price")
    special_instructions = fields.Text(readonly=True)
    webhook_payload = fields.Text(readonly=True)
    order_payload = fields.Text(string="Order details (Uber)", readonly=True)
    error_message = fields.Text(readonly=True)
    attempts = fields.Integer(default=0, readonly=True)
    simulated = fields.Boolean(
        help="Test order created from Odoo: no calls are made to Uber.", readonly=True)

    _sql_constraints = [
        ("uber_order_id_uniq", "unique(uber_order_id)", "This Uber Eats order was already received."),
    ]

    # ---------------------------------------------------------------- webhook
    @api.model
    def _from_webhook(self, event):
        """Register a webhook event. Returns the record to process, or empty."""
        event_type = event.get("event_type") or event.get("type") or ""
        meta = event.get("meta") or {}
        order_id = meta.get("resource_id") or event.get("order_id") or event.get("resource_id")
        store_id = meta.get("user_id") or event.get("store_id") or ""
        if not order_id:
            _logger.warning("Uber Eats webhook without order id ignored: %s", event_type)
            return self.browse()

        record = self.search([("uber_order_id", "=", order_id)], limit=1)
        if event_type in ACTIVE_EVENTS:
            if not record:
                record = self.create({
                    "uber_order_id": order_id,
                    "store_id": store_id,
                    "event_type": event_type,
                    "webhook_payload": json.dumps(event),
                })
            elif record.state == "error":
                record.write({"state": "received", "event_type": event_type, "error_message": False})
            elif record.state == "accepted" and event_type == "orders.release":
                record._notify_pos()
            return record
        if event_type in CANCEL_EVENTS:
            if record:
                record._handle_cancel(event)
            else:
                _logger.info("Uber Eats cancel for unknown order %s ignored", order_id)
            return self.browse()
        _logger.info("Uber Eats event %s ignored", event_type)
        return self.browse()

    def _process_in_new_cursor(self):
        """Process outside the webhook transaction so the HTTP reply is not
        held back by, or lost to, a failure in order creation."""
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env["uber.eats.order"].browse(self.ids)._process()

    # ------------------------------------------------------------- processing
    def _process(self):
        for record in self.filtered(lambda r: r.state in ("received", "error")):
            try:
                with self.env.cr.savepoint():
                    record._do_process()
            except Exception as exc:  # noqa: BLE001 - recorded on the order, retried by cron
                _logger.exception("Uber Eats order %s failed", record.uber_order_id)
                record.write({
                    "state": "error",
                    "error_message": str(exc)[:2000],
                    "attempts": record.attempts + 1,
                })

    def _do_process(self):
        self.ensure_one()
        api_client = self.env["uber.eats.api"]
        config = self._find_config()
        data = json.loads(self.order_payload) if self.order_payload else None
        if data is None:
            data = api_client.get_order(self.uber_order_id)
            self.order_payload = json.dumps(data)
        parsed = self._parse(data)
        self.write({
            "pos_config_id": config.id,
            "display_id": parsed["display_id"],
            "store_id": self.store_id or parsed["store_id"],
            "order_type": parsed["type"],
            "eater_name": parsed["eater_name"],
            "placed_at": parsed["placed_at"],
            "ready_at": parsed["ready_at"],
            "amount_uber": parsed["items_total"],
            "special_instructions": parsed["special_instructions"],
        })

        session = config.current_session_id
        if not session or session.state not in ("opening_control", "opened"):
            if config.uber_eats_deny_when_closed:
                if not self.simulated:
                    api_client.deny_order(self.uber_order_id, "Store is closed", "STORE_CLOSED")
                self.write({"state": "denied",
                            "error_message": _("No open POS session on %s: order denied.", config.name)})
                return
            raise UserError(_("No open POS session on %s; will retry.", config.name))

        pos_order = self._create_pos_order(config, session, parsed)
        if not self.simulated:
            api_client.accept_order(self.uber_order_id, pos_order.pos_reference)
        self.write({
            "state": "accepted",
            "pos_order_id": pos_order.id,
            "amount_pos": pos_order.amount_total,
            "error_message": False,
        })
        self._notify_pos()

    def _find_config(self):
        self.ensure_one()
        configs = self.env["pos.config"].search([("uber_eats_enabled", "=", True)])
        if not configs:
            raise UserError(_("No point of sale has 'Receive Uber Eats orders' enabled."))
        if self.store_id:
            matching = configs.filtered(lambda c: c.uber_eats_store_id and c.uber_eats_store_id == self.store_id)
            if matching:
                return matching[0]
        if len(configs) == 1:
            return configs
        raise UserError(_("Several points of sale receive Uber Eats orders but none has store ID %s.", self.store_id))

    # ---------------------------------------------------------------- parsing
    @api.model
    def _parse(self, data):
        cart = data.get("cart") or (data.get("carts") or [{}])[0] or {}
        items = cart.get("items") or []
        eater = data.get("eater") or (data.get("eaters") or [{}])[0] or {}
        eater_name = " ".join(filter(None, [eater.get("first_name"), eater.get("last_name")])).strip()
        charges = (data.get("payment") or {}).get("charges") or {}
        items_total = _money(charges.get("sub_total")) if charges.get("sub_total") else sum(
            self._item_gross(item) for item in items)
        order_type = data.get("type") or "unknown"
        if order_type not in dict(ORDER_TYPES):
            order_type = "unknown"
        return {
            "display_id": data.get("display_id") or data.get("id", "")[:8],
            "store_id": (data.get("store") or {}).get("id", ""),
            "type": order_type,
            "eater_name": eater_name,
            "placed_at": _iso_to_datetime(data.get("placed_at")),
            "ready_at": _iso_to_datetime(data.get("estimated_ready_for_pickup_at")),
            "items": items,
            "items_total": items_total,
            "special_instructions": cart.get("special_instructions") or data.get("special_instructions") or "",
            "scheduled": bool(data.get("scheduled_order") or data.get("is_scheduled")),
        }

    @api.model
    def _item_gross(self, item):
        price = item.get("price") or {}
        if price.get("total_price"):
            return _money(price["total_price"])
        qty = item.get("quantity") or 1
        return _money(price.get("unit_price")) * qty

    # ------------------------------------------------------------ POS order
    def _create_pos_order(self, config, session, parsed):
        self.ensure_one()
        partner = config._get_uber_partner()
        payment_method = config.uber_eats_payment_method_id
        if not payment_method or payment_method not in config.payment_method_ids:
            try:
                payment_method = config._get_uber_payment_method()
            except UserError as exc:
                raise UserError(_(
                    "The 'Uber Eats' payment method is not attached to %(pos)s and cannot be "
                    "added while its session is open. Close the session, re-save the Uber Eats "
                    "settings, and open it again.\n\n%(detail)s",
                    pos=config.name, detail=exc.args[0],
                )) from exc
        lines = self._prepare_lines(config, parsed["items"], partner)
        if not lines:
            raise UserError(_("Uber Eats order %s has no items.", parsed["display_id"]))

        total_excl = sum(line["price_subtotal"] for line in lines)
        total_incl = sum(line["price_subtotal_incl"] for line in lines)
        sequence_number = self._next_sequence_number(session)
        pos_reference = f"Uber Eats {session.id:05d}-{config.id:03d}-{sequence_number:04d}"

        type_label = dict(ORDER_TYPES).get(parsed["type"], parsed["type"])
        note_parts = [f"UBER EATS #{parsed['display_id']}", type_label]
        if parsed["eater_name"]:
            note_parts.append(parsed["eater_name"])
        if parsed["ready_at"]:
            local = fields.Datetime.context_timestamp(self.with_context(tz=config.company_id.partner_id.tz or "UTC"), parsed["ready_at"])
            note_parts.append(_("ready by %s", local.strftime("%H:%M")))
        if parsed["scheduled"]:
            note_parts.append(_("SCHEDULED"))
        if parsed["special_instructions"]:
            note_parts.append(parsed["special_instructions"])

        order = self.env["pos.order"].with_company(config.company_id).create({
            "session_id": session.id,
            "company_id": config.company_id.id,
            "partner_id": partner.id,
            "pos_reference": pos_reference,
            "sequence_number": sequence_number,
            "floating_order_name": f"Uber Eats #{parsed['display_id']}",
            "uuid": str(uuid.uuid4()),
            "takeaway": True,
            "general_note": " | ".join(note_parts),
            "date_order": parsed["placed_at"] or fields.Datetime.now(),
            "lines": [(0, 0, vals) for vals in lines],
            "amount_tax": total_incl - total_excl,
            "amount_total": total_incl,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        })
        order.add_payment({
            "pos_order_id": order.id,
            "payment_method_id": payment_method.id,
            "amount": total_incl,
            "name": f"Uber Eats #{parsed['display_id']}",
        })
        if config.uber_eats_order_state == "paid":
            order.action_pos_order_paid()
        return order

    def _next_sequence_number(self, session):
        sequence = self.env["ir.sequence"].with_context(company_id=session.company_id.id).next_by_code(f"pos.order_{session.id}")
        digits = re.findall(r"\d+", sequence or "")
        if digits:
            return int(digits[0])
        return len(session.order_ids) + 1

    def _prepare_lines(self, config, items, partner):
        fiscal_position = config.default_fiscal_position_id
        lines = []
        for item in items:
            qty = float(item.get("quantity") or 1)
            product = self._match_product(config, item)
            gross = self._item_gross(item)
            notes = []
            if item.get("special_instructions"):
                notes.append(item["special_instructions"])
            # Modifiers: their own line when they map to a real product, otherwise
            # folded into the parent line's price and noted.
            for group in item.get("selected_modifier_groups") or []:
                for choice in group.get("selected_items") or []:
                    choice_qty = float(choice.get("quantity") or 1)
                    choice_gross = self._item_gross(choice) * (1 if choice.get("price", {}).get("total_price") else qty)
                    choice_product = self._match_product(config, choice, allow_fallback=False)
                    if choice_product and choice_gross:
                        lines.append(self._line_vals(config, fiscal_position, partner, choice_product,
                                                     choice_qty * qty, choice_gross, choice.get("title"),
                                                     _("for %s", item.get("title", ""))))
                    else:
                        gross += choice_gross
                        notes.append(f"+ {choice.get('title', '')}")
            lines.append(self._line_vals(config, fiscal_position, partner, product, qty, gross,
                                         item.get("title"), "; ".join(notes)))
        return lines

    def _line_vals(self, config, fiscal_position, partner, product, qty, gross, title, note):
        taxes = product.taxes_id.filtered(lambda t: t.company_id == config.company_id)
        if fiscal_position:
            taxes = fiscal_position.map_tax(taxes)
        price_unit = self._price_unit_for(taxes, gross, qty)
        amounts = taxes.compute_all(price_unit, config.currency_id, qty, product=product, partner=partner)
        return {
            "product_id": product.id,
            "full_product_name": title or product.display_name,
            "qty": qty,
            "price_unit": price_unit,
            "discount": 0.0,
            "tax_ids": [(6, 0, taxes.ids)],
            "price_subtotal": amounts["total_excluded"],
            "price_subtotal_incl": amounts["total_included"],
            "customer_note": note or False,
            "uuid": str(uuid.uuid4()),
        }

    @api.model
    def _price_unit_for(self, taxes, gross, qty):
        """Unit price such that the line's tax-inclusive total equals Uber's
        (Uber Eats prices in Australia include GST)."""
        qty = qty or 1.0
        excluded = taxes.filtered(lambda t: not t.price_include and t.amount_type == "percent")
        if not taxes or not excluded:
            return gross / qty
        return gross / qty / (1 + sum(excluded.mapped("amount")) / 100.0)

    def _match_product(self, config, item, allow_fallback=True):
        Product = self.env["product.product"]
        base = [("available_in_pos", "=", True), ("company_id", "in", [False, config.company_id.id])]
        external = (item.get("external_data") or "").strip()
        uber_id = (item.get("id") or "").strip()
        title = (item.get("title") or "").strip()
        candidates = []
        if external:
            candidates.append([("default_code", "=", external)])
            candidates.append([("uber_eats_id", "=", external)])
        if uber_id:
            candidates.append([("uber_eats_id", "=", uber_id)])
        if title:
            candidates.append([("name", "=ilike", title)])
        for domain in candidates:
            product = Product.search(base + domain, limit=1)
            if product:
                return product
        if not allow_fallback:
            return Product
        return config._get_uber_fallback_product()

    # ----------------------------------------------------------- lifecycle
    def _handle_cancel(self, event):
        for record in self:
            order = record.pos_order_id
            if order and order.state == "draft":
                order.write({"state": "cancel"})
            elif order:
                order.write({"general_note": (order.general_note or "") + " | " + _("CANCELLED ON UBER EATS")})
            record.write({"state": "cancelled", "webhook_payload": json.dumps(event)})
            record._notify_pos()

    def _notify_pos(self):
        for record in self.filtered("pos_config_id"):
            session = record.pos_config_id.current_session_id
            if session:
                record.pos_config_id.notify_synchronisation(session.id, 0)

    @api.model
    def _cron_process_pending(self):
        since = fields.Datetime.now() - RETRY_WINDOW
        pending = self.search([
            ("state", "in", ("received", "error")),
            ("attempts", "<", MAX_ATTEMPTS),
            ("create_date", ">=", since),
        ])
        for record in pending:
            record._process()
            if not getattr(threading.current_thread(), "testing", False):
                self.env.cr.commit()  # keep each order's outcome even if a later one fails

    # ---------------------------------------------------------------- actions
    def action_retry(self):
        self.filtered(lambda r: r.state in ("error", "denied")).write({"state": "received", "error_message": False})
        self._process()

    def action_view_pos_order(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "pos.order",
            "res_id": self.pos_order_id.id,
            "view_mode": "form",
        }

    @api.model
    def action_create_test_order(self):
        """Simulate an incoming Uber Eats order without contacting Uber, so the
        intake can be checked end to end (POS screen, receipt, accounting)."""
        config = self.env["pos.config"].search([("uber_eats_enabled", "=", True)], limit=1)
        if not config:
            raise UserError(_("Enable 'Receive Uber Eats orders' on a point of sale first "
                              "(Point of Sale > Configuration > Settings > Uber Eats)."))
        products = self.env["product.product"].search([
            ("available_in_pos", "=", True), ("list_price", ">", 0),
            ("company_id", "in", [False, config.company_id.id]),
            ("default_code", "!=", "UBER-ITEM"),
        ], limit=2)
        items = []
        for index, product in enumerate(products):
            items.append({
                "id": f"test-item-{product.id}",
                "title": product.name,
                "external_data": product.default_code or "",
                "quantity": index + 1,
                "price": {"unit_price": {"amount": int(round(product.lst_price * 100))},
                          "total_price": {"amount": int(round(product.lst_price * 100)) * (index + 1)}},
                "special_instructions": "No onions" if index == 0 else "",
            })
        if not items:
            items = [{
                "id": "test-item-1", "title": "Margherita Pizza", "external_data": "", "quantity": 1,
                "price": {"unit_price": {"amount": 1800}, "total_price": {"amount": 1800}},
            }]
        test_id = "TEST-" + uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        payload = {
            "id": test_id,
            "display_id": test_id[-5:].upper(),
            "type": "PICKUP",
            "store": {"id": config.uber_eats_store_id or "test-store"},
            "eater": {"first_name": "Test", "last_name": "Customer", "phone": ""},
            "cart": {"items": items, "special_instructions": "Test order created from Odoo"},
            "payment": {"charges": {"sub_total": {"amount": sum(i["price"]["total_price"]["amount"] for i in items)}}},
            "placed_at": now.isoformat().replace("+00:00", "Z"),
            "estimated_ready_for_pickup_at": (now + timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
        }
        record = self.create({
            "uber_order_id": test_id,
            "store_id": config.uber_eats_store_id or "",
            "event_type": "orders.notification",
            "simulated": True,
            "webhook_payload": json.dumps({"event_type": "orders.notification",
                                           "meta": {"resource_id": test_id, "status": "pos"}}),
            "order_payload": json.dumps(payload),
        })
        record._process()
        return {
            "type": "ir.actions.act_window",
            "res_model": "uber.eats.order",
            "res_id": record.id,
            "view_mode": "form",
        }
