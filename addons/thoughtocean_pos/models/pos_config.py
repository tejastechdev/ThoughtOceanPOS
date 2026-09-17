from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = "pos.config"

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        configs.filtered("uber_eats_enabled")._ensure_uber_payment_method()
        return configs

    def write(self, vals):
        result = super().write(vals)
        if vals.get("uber_eats_enabled"):
            self._ensure_uber_payment_method()
        return result

    def _ensure_uber_payment_method(self):
        """Attach the Uber Eats payment method while no session is open: Odoo
        refuses payment-method changes on a point of sale with an open session."""
        for config in self:
            try:
                config._get_uber_payment_method()
            except UserError as exc:
                raise UserError(_(
                    "Close the session of %(pos)s before enabling Uber Eats, so the "
                    "'Uber Eats' payment method can be added to it.\n\n%(detail)s",
                    pos=config.name, detail=exc.args[0],
                )) from exc

    uber_eats_enabled = fields.Boolean(
        string="Receive Uber Eats orders",
        help="Orders placed on Uber Eats for the store below are created in this point of sale.",
    )
    uber_eats_store_id = fields.Char(
        string="Uber Eats store ID",
        help="Store UUID from Uber Eats Manager. Leave empty if only one point of sale receives Uber Eats orders.",
    )
    uber_eats_order_state = fields.Selection(
        [("draft", "Open order: staff prepare it and validate"), ("paid", "Finalise automatically as paid")],
        string="Incoming orders are",
        default="draft",
        required=True,
    )
    uber_eats_deny_when_closed = fields.Boolean(
        string="Deny orders when no session is open",
        default=True,
        help="If no POS session is open the order is denied on Uber Eats with reason 'store closed'. "
             "Otherwise it is kept and retried for a few minutes.",
    )
    uber_eats_fallback_product_id = fields.Many2one(
        "product.product",
        string="Fallback product",
        help="Used for Uber Eats items that match none of your products (by Internal Reference, Uber Eats item ID or name).",
        domain="[('available_in_pos', '=', True)]",
    )
    uber_eats_payment_method_id = fields.Many2one("pos.payment.method", string="Uber Eats payment method")

    def _get_uber_partner(self):
        return self.env.ref("thoughtocean_pos.partner_uber_eats", raise_if_not_found=False) or self.env["res.partner"]

    def _get_uber_fallback_product(self):
        self.ensure_one()
        if not self.uber_eats_fallback_product_id:
            product = self.env["product.product"].search([
                ("default_code", "=", "UBER-ITEM"),
                ("company_id", "in", [False, self.company_id.id]),
            ], limit=1)
            if not product:
                product = self.env["product.product"].create({
                    "name": _("Uber Eats item"),
                    "default_code": "UBER-ITEM",
                    "type": "consu",
                    "available_in_pos": True,
                    "list_price": 0.0,
                    "company_id": self.company_id.id,
                })
            self.uber_eats_fallback_product_id = product
        return self.uber_eats_fallback_product_id

    def _get_uber_payment_method(self):
        """Bank-type payment method so Uber Eats orders are recorded as paid
        (Uber settles with the restaurant separately)."""
        self.ensure_one()
        method = self.uber_eats_payment_method_id
        if not method:
            method = self.env["pos.payment.method"].search([
                ("name", "=", "Uber Eats"), ("company_id", "=", self.company_id.id),
            ], limit=1)
        if not method:
            journal = self.env["account.journal"].search([
                ("code", "=", "UBER"), ("company_id", "=", self.company_id.id),
            ], limit=1)
            if not journal:
                journal = self.env["account.journal"].create({
                    "name": "Uber Eats",
                    "code": "UBER",
                    "type": "bank",
                    "company_id": self.company_id.id,
                })
            method = self.env["pos.payment.method"].create({
                "name": "Uber Eats",
                "journal_id": journal.id,
                "company_id": self.company_id.id,
            })
        if method not in self.payment_method_ids:
            self.write({"payment_method_ids": [(4, method.id)]})
        if self.uber_eats_payment_method_id != method:
            self.uber_eats_payment_method_id = method
        return method
