from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Shared credentials (one Uber Eats developer application per company/system)
    uber_client_id = fields.Char(string="Uber Eats client ID", config_parameter="thoughtocean_pos.uber_client_id")
    uber_client_secret = fields.Char(string="Uber Eats client secret", config_parameter="thoughtocean_pos.uber_client_secret")
    uber_webhook_url = fields.Char(string="Webhook URL", compute="_compute_uber_webhook_url")

    # Per point of sale
    pos_uber_eats_enabled = fields.Boolean(related="pos_config_id.uber_eats_enabled", readonly=False)
    pos_uber_eats_store_id = fields.Char(related="pos_config_id.uber_eats_store_id", readonly=False)
    pos_uber_eats_order_state = fields.Selection(related="pos_config_id.uber_eats_order_state", readonly=False)
    pos_uber_eats_deny_when_closed = fields.Boolean(related="pos_config_id.uber_eats_deny_when_closed", readonly=False)
    pos_uber_eats_fallback_product_id = fields.Many2one(related="pos_config_id.uber_eats_fallback_product_id", readonly=False)

    @api.depends("company_id")
    def _compute_uber_webhook_url(self):
        url = self.env["uber.eats.api"].webhook_url()
        for rec in self:
            rec.uber_webhook_url = url
