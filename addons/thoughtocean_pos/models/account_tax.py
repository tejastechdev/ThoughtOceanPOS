from odoo import _, fields, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    uber_eats_included_tax_id = fields.Many2one(
        "account.tax", string="Tax-included twin (Uber Eats)", copy=False, readonly=True,
        help="Same rate but with the price including the tax; used for Uber Eats lines, "
             "whose prices are quoted tax-inclusive.",
    )

    def _uber_eats_included(self):
        """Tax-included equivalent of this tax, created on first use.

        Uber quotes gross prices. Pricing POS lines tax-inclusive keeps the unit
        price at two decimals, so the POS front end (which rounds unit prices to
        cents) and the server agree with Uber to the cent."""
        self.ensure_one()
        if self.price_include:
            return self
        twin = self.uber_eats_included_tax_id
        if not twin or not twin.active:
            twin = self.copy({
                "name": _("%s (Uber Eats, tax incl.)", self.name),
                "price_include_override": "tax_included",
                "active": True,
                "uber_eats_included_tax_id": False,
            })
            self.uber_eats_included_tax_id = twin
        return twin

    def _uber_eats_included_set(self):
        result = self.browse()
        for tax in self:
            result |= tax._uber_eats_included()
        return result
