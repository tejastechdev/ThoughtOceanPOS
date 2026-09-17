from odoo import fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    uber_eats_order_ids = fields.One2many("uber.eats.order", "pos_order_id", string="Uber Eats orders")
    is_uber_eats = fields.Boolean(compute="_compute_is_uber_eats", search="_search_is_uber_eats", string="From Uber Eats")

    def _compute_is_uber_eats(self):
        for order in self:
            order.is_uber_eats = bool(order.uber_eats_order_ids)

    def _search_is_uber_eats(self, operator, value):
        wanted = (operator == "=" and value) or (operator == "!=" and not value)
        return [("uber_eats_order_ids", "!=" if wanted else "=", False)]
