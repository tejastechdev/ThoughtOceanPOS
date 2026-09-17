import json
import logging

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class UberEatsWebhookController(http.Controller):
    @http.route("/uber_eats/webhook", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def uber_eats_webhook(self, **kwargs):
        raw = request.httprequest.get_data()
        signature = request.httprequest.headers.get("X-Uber-Signature", "")
        api_client = request.env["uber.eats.api"].sudo()
        if not api_client.verify_signature(raw, signature):
            _logger.warning("Uber Eats webhook rejected: bad or missing signature")
            return Response("invalid signature", status=401)
        try:
            event = json.loads(raw or b"{}")
        except ValueError:
            return Response("invalid json", status=400)
        if not isinstance(event, dict):
            return Response("invalid payload", status=400)

        record = request.env["uber.eats.order"].sudo()._from_webhook(event)
        # Persist the event before doing any slow work so Uber gets a 200 even if
        # order creation needs a retry.
        request.env.cr.commit()
        if record and record.state == "received":
            try:
                record._process_in_new_cursor()
            except Exception:  # noqa: BLE001 - never fail the webhook; the cron retries
                _logger.exception("Uber Eats order %s: processing failed", record.uber_order_id)
        return Response("ok", status=200)
