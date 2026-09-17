import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.thoughtocean_pos.models.uber_eats_api import UberEatsApi

SECRET = "test-secret"


def sample_order(order_id="uber-1", display_id="AB12C", items=None):
    items = items if items is not None else [
        {
            "id": "item-pizza", "title": "Margherita Pizza", "external_data": "PIZ-MARG", "quantity": 1,
            "price": {"unit_price": {"amount": 1200}, "total_price": {"amount": 1200}},
            "selected_modifier_groups": [{
                "title": "Extras",
                "selected_items": [{"id": "mod-chilli", "title": "Extra chilli", "quantity": 1,
                                    "price": {"unit_price": {"amount": 0}, "total_price": {"amount": 0}}}],
            }],
            "special_instructions": "Well done",
        },
        {
            "id": "item-unknown", "title": "Mystery Dessert", "external_data": "", "quantity": 2,
            "price": {"unit_price": {"amount": 650}, "total_price": {"amount": 1300}},
        },
    ]
    return {
        "id": order_id,
        "display_id": display_id,
        "type": "PICKUP",
        "store": {"id": "store-1"},
        "eater": {"first_name": "Jane", "last_name": "Doe"},
        "cart": {"items": items, "special_instructions": "Ring the bell"},
        "payment": {"charges": {"sub_total": {"amount": sum(i["price"]["total_price"]["amount"] for i in items)}}},
        "placed_at": "2026-09-17T09:00:00Z",
        "estimated_ready_for_pickup_at": "2026-09-17T09:25:00Z",
    }


def notification(order_id="uber-1", event_type="orders.notification"):
    return {"event_type": event_type, "event_id": "evt-1", "event_time": 1789000000,
            "meta": {"resource_id": order_id, "status": "pos", "user_id": "store-1"},
            "resource_href": f"https://api.uber.com/v2/eats/order/{order_id}"}


def sign(body, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class UberEatsCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("thoughtocean_pos.uber_client_id", "cid")
        cls.env["ir.config_parameter"].sudo().set_param("thoughtocean_pos.uber_client_secret", SECRET)
        cls.company = cls.env.company
        cls.tax = cls.env["account.tax"].create({
            "name": "GST 10% (test)", "amount": 10, "amount_type": "percent", "type_tax_use": "sale",
            "company_id": cls.company.id,
        })
        cls.pizza = cls.env["product.product"].create({
            "name": "Margherita Pizza", "default_code": "PIZ-MARG", "type": "consu",
            "available_in_pos": True, "list_price": 15.0, "taxes_id": [(6, 0, cls.tax.ids)],
        })
        cls.config = cls.env["pos.config"].create({
            "name": "Uber test POS", "uber_eats_enabled": True, "uber_eats_store_id": "store-1",
        })
        cls.config.open_ui()
        cls.session = cls.config.current_session_id
        if cls.session.state == "opening_control":
            cls.session.set_opening_control(0, "")

    def _receive(self, order=None, event=None):
        order = order or sample_order()
        calls = {"accept": [], "deny": []}
        with patch.object(UberEatsApi, "get_order", lambda self, oid: order), \
             patch.object(UberEatsApi, "accept_order", lambda self, oid, ref: calls["accept"].append((oid, ref)) or {}), \
             patch.object(UberEatsApi, "deny_order", lambda self, oid, expl, code="OTHER": calls["deny"].append((oid, code)) or {}):
            record = self.env["uber.eats.order"]._from_webhook(event or notification(order["id"]))
            record._process()
        return record, calls


@tagged("post_install", "-at_install")
class TestUberEatsIntake(UberEatsCase):
    def test_signature(self):
        api = self.env["uber.eats.api"]
        body = b'{"a":1}'
        self.assertTrue(api.verify_signature(body, sign(body)))
        self.assertTrue(api.verify_signature(body, sign(body).upper()))
        self.assertFalse(api.verify_signature(body, sign(body, "other")))
        self.assertFalse(api.verify_signature(body, ""))

    def test_order_created_and_accepted(self):
        record, calls = self._receive()
        self.assertEqual(record.state, "accepted", record.error_message)
        self.assertEqual(record.pos_config_id, self.config)
        self.assertEqual(record.display_id, "AB12C")
        self.assertEqual(record.eater_name, "Jane Doe")
        self.assertEqual(record.order_type, "PICKUP")
        self.assertAlmostEqual(record.amount_uber, 25.0, 2)

        order = record.pos_order_id
        self.assertTrue(order)
        self.assertEqual(order.session_id, self.session)
        self.assertEqual(order.state, "draft", "default mode keeps the order open for staff")
        self.assertTrue(order.takeaway)
        self.assertIn("UBER EATS #AB12C", order.general_note)
        self.assertIn("Jane Doe", order.general_note)
        self.assertIn("Ring the bell", order.general_note)
        self.assertTrue(order.pos_reference.startswith("Uber Eats "))
        self.assertEqual(order.floating_order_name, "Uber Eats #AB12C")
        self.assertEqual(order.partner_id, self.env.ref("thoughtocean_pos.partner_uber_eats"))

        self.assertEqual(len(order.lines), 2)
        pizza_line = order.lines.filtered(lambda l: l.product_id == self.pizza)
        self.assertEqual(len(pizza_line), 1, "matched by internal reference")
        self.assertAlmostEqual(pizza_line.price_subtotal_incl, 12.0, 2, "Uber gross price kept")
        self.assertAlmostEqual(pizza_line.price_subtotal, 12.0 / 1.1, 2, "GST backed out of the gross price")
        self.assertAlmostEqual(pizza_line.price_unit, 12.0, 2, "priced tax-inclusive at two decimals")
        self.assertEqual(pizza_line.tax_ids, self.tax.uber_eats_included_tax_id, "tax-included twin of the product tax")
        self.assertTrue(pizza_line.tax_ids.price_include)
        self.assertEqual(pizza_line.tax_ids.amount, 10)
        self.assertIn("Well done", pizza_line.customer_note)
        self.assertIn("Extra chilli", pizza_line.customer_note)
        other = order.lines - pizza_line
        self.assertEqual(other.product_id, self.config.uber_eats_fallback_product_id, "unknown item uses fallback")
        self.assertEqual(other.full_product_name, "Mystery Dessert")
        self.assertEqual(other.qty, 2)
        self.assertAlmostEqual(other.price_subtotal_incl, 13.0, 2)

        self.assertAlmostEqual(order.amount_total, 25.0, 2)
        self.assertAlmostEqual(order.amount_paid, 25.0, 2)
        self.assertEqual(len(order.payment_ids), 1)
        self.assertEqual(order.payment_ids.payment_method_id.name, "Uber Eats")
        self.assertIn(order.payment_ids.payment_method_id, self.config.payment_method_ids)
        self.assertAlmostEqual(record.amount_pos, 25.0, 2)

        self.assertEqual(calls["accept"], [("uber-1", order.pos_reference)])
        self.assertFalse(calls["deny"])
        self.assertTrue(order.is_uber_eats)

    def test_paid_mode_finalises(self):
        self.config.uber_eats_order_state = "paid"
        record, _calls = self._receive()
        self.assertEqual(record.state, "accepted", record.error_message)
        self.assertEqual(record.pos_order_id.state, "paid")

    def test_duplicate_webhook_is_idempotent(self):
        record, _ = self._receive()
        again, calls = self._receive()
        self.assertEqual(record, again)
        self.assertEqual(self.env["pos.order"].search_count([("uber_eats_order_ids", "in", record.ids)]), 1)
        self.assertFalse(calls["accept"], "already accepted orders are not re-sent to Uber")

    def test_no_session_denies(self):
        closed_config = self.env["pos.config"].create({
            "name": "Closed POS", "uber_eats_enabled": True, "uber_eats_store_id": "store-2",
        })
        event = notification("uber-2")
        event["meta"]["user_id"] = "store-2"
        record, calls = self._receive(sample_order("uber-2"), event)
        self.assertEqual(record.pos_config_id, closed_config)
        self.assertEqual(record.state, "denied")
        self.assertEqual(calls["deny"], [("uber-2", "STORE_CLOSED")])
        self.assertFalse(record.pos_order_id)

    @mute_logger("odoo.addons.thoughtocean_pos.models.uber_eats_order")
    def test_no_session_keeps_for_retry_when_configured(self):
        self.env["pos.config"].create({
            "name": "Closed POS", "uber_eats_enabled": True, "uber_eats_store_id": "store-2",
            "uber_eats_deny_when_closed": False,
        })
        event = notification("uber-3")
        event["meta"]["user_id"] = "store-2"
        record, calls = self._receive(sample_order("uber-3"), event)
        self.assertEqual(record.state, "error")
        self.assertIn("No open POS session", record.error_message)
        self.assertEqual(record.attempts, 1)
        self.assertFalse(calls["deny"])

    def test_cancel_event(self):
        record, _ = self._receive()
        order = record.pos_order_id
        self.env["uber.eats.order"]._from_webhook(notification("uber-1", "orders.cancel"))
        self.assertEqual(record.state, "cancelled")
        self.assertEqual(order.state, "cancel")

    def test_unknown_event_ignored(self):
        result = self.env["uber.eats.order"]._from_webhook({"event_type": "store.status", "meta": {"resource_id": "x"}})
        self.assertFalse(result)
        self.assertFalse(self.env["uber.eats.order"].search([("uber_order_id", "=", "x")]))

    def test_price_unit_with_included_tax(self):
        incl_tax = self.env["account.tax"].create({
            "name": "GST incl (test)", "amount": 10, "amount_type": "percent", "type_tax_use": "sale",
            "price_include_override": "tax_included", "company_id": self.company.id,
        })
        self.pizza.taxes_id = [(6, 0, incl_tax.ids)]
        record, _ = self._receive()
        line = record.pos_order_id.lines.filtered(lambda l: l.product_id == self.pizza)
        self.assertAlmostEqual(line.price_unit, 12.0, 2)
        self.assertAlmostEqual(line.price_subtotal_incl, 12.0, 2)

    def test_line_totals_match_uber_to_the_cent(self):
        # 32.50 for qty 2 with 10% tax backed out per unit rounds to 32.51 (server) / 32.49 (POS screen);
        # tax-inclusive pricing keeps everyone at 32.50
        items = [{
            "id": "item-pizza", "title": "Margherita Pizza", "external_data": "PIZ-MARG", "quantity": 2,
            "price": {"unit_price": {"amount": 1625}, "total_price": {"amount": 3250}},
        }, {
            "id": "item-x", "title": "Odd priced item", "external_data": "", "quantity": 3,
            "price": {"unit_price": {"amount": 1111}, "total_price": {"amount": 3333}},
        }]
        record, _ = self._receive(sample_order(items=items))
        self.assertEqual(record.state, "accepted", record.error_message)
        order = record.pos_order_id
        totals = sorted(round(l.price_subtotal_incl, 2) for l in order.lines)
        self.assertEqual(totals, [32.5, 33.33])
        units = sorted(round(l.price_unit, 4) for l in order.lines)
        self.assertEqual(units, [11.11, 16.25], "unit prices are whole cents so the POS screen computes the same totals")
        self.assertTrue(all(t.price_include for t in order.lines.tax_ids))
        self.assertEqual(self.tax.uber_eats_included_tax_id.uber_eats_included_tax_id, self.env["account.tax"],
                         "twin is created once and not chained")
        self.assertAlmostEqual(order.amount_total, 65.83, 2)
        self.assertAlmostEqual(order.amount_paid, 65.83, 2)
        self.assertAlmostEqual(record.amount_uber, record.amount_pos, 2)

    def test_test_order_button(self):
        action = self.env["uber.eats.order"].action_create_test_order()
        record = self.env["uber.eats.order"].browse(action["res_id"])
        self.assertTrue(record.simulated)
        self.assertEqual(record.state, "accepted", record.error_message)
        self.assertTrue(record.pos_order_id.lines)


@tagged("post_install", "-at_install")
class TestUberEatsWebhook(HttpCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("thoughtocean_pos.uber_client_secret", SECRET)
        self.env["ir.config_parameter"].sudo().set_param("thoughtocean_pos.uber_client_id", "cid")

    def test_rejects_bad_signature(self):
        body = json.dumps(notification("uber-http-1")).encode()
        resp = self.url_open("/uber_eats/webhook", data=body, headers={
            "Content-Type": "application/json", "X-Uber-Signature": sign(body, "wrong")})
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(self.env["uber.eats.order"].search([("uber_order_id", "=", "uber-http-1")]))

    def test_accepts_signed_event(self):
        config = self.env["pos.config"].create({"name": "Webhook POS", "uber_eats_enabled": True})
        body = json.dumps(notification("uber-http-2")).encode()
        with patch.object(UberEatsApi, "get_order", lambda self, oid: sample_order("uber-http-2")), \
             patch.object(UberEatsApi, "accept_order", lambda self, oid, ref: {}), \
             patch.object(UberEatsApi, "deny_order", lambda self, oid, expl, code="OTHER": {}):
            resp = self.url_open("/uber_eats/webhook", data=body, headers={
                "Content-Type": "application/json", "X-Uber-Signature": sign(body)})
        self.assertEqual(resp.status_code, 200)
        record = self.env["uber.eats.order"].search([("uber_order_id", "=", "uber-http-2")])
        self.assertTrue(record)
        self.assertEqual(record.pos_config_id, config)
        # no session open on this config, so the order is denied but the event was recorded
        self.assertEqual(record.state, "denied")
