from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    uber_eats_id = fields.Char(
        string="Uber Eats item ID",
        help="Item ID (or external data) as it appears in your Uber Eats menu. "
             "Incoming Uber Eats items are matched to products by Internal Reference, "
             "then by this ID, then by exact name.",
    )
