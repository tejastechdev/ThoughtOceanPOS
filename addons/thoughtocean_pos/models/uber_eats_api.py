"""Thin client for the Uber Eats Marketplace API.

Only the calls the order intake needs are implemented: OAuth2 client-credentials
token, get order details, accept / deny an order, and webhook signature
verification. Endpoints and scopes are system parameters so they can be adjusted
without a code change if Uber moves them.
"""
import hashlib
import hmac
import logging
import time

import requests

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PARAM_DEFAULTS = {
    "thoughtocean_pos.uber_auth_url": "https://auth.uber.com/oauth/v2/token",
    "thoughtocean_pos.uber_api_base": "https://api.uber.com",
    "thoughtocean_pos.uber_scopes": "eats.store eats.order eats.store.orders.read",
    "thoughtocean_pos.uber_get_order_path": "/v2/eats/order/{order_id}",
    "thoughtocean_pos.uber_accept_path": "/v1/eats/orders/{order_id}/accept_pos_order",
    "thoughtocean_pos.uber_deny_path": "/v1/eats/orders/{order_id}/deny_pos_order",
}
TIMEOUT = 20


class UberEatsApi(models.AbstractModel):
    _name = "uber.eats.api"
    _description = "Uber Eats Marketplace API client"

    # ------------------------------------------------------------------ config
    @api.model
    def _param(self, key, default=""):
        return self.env["ir.config_parameter"].sudo().get_param(key, PARAM_DEFAULTS.get(key, default))

    @api.model
    def _credentials(self):
        client_id = self._param("thoughtocean_pos.uber_client_id")
        secret = self._param("thoughtocean_pos.uber_client_secret")
        if not client_id or not secret:
            raise UserError(_(
                "Uber Eats is not configured: set the client ID and client secret under "
                "Point of Sale > Configuration > Settings > Uber Eats."
            ))
        return client_id, secret

    @api.model
    def webhook_url(self):
        base = self._param("web.base.url").rstrip("/")
        return f"{base}/uber_eats/webhook"

    # --------------------------------------------------------------- security
    @api.model
    def verify_signature(self, raw_body, signature, secret=None):
        """Uber signs webhook bodies with HMAC-SHA256 using the client secret
        and sends the lowercase hex digest in the X-Uber-Signature header."""
        secret = secret or self._param("thoughtocean_pos.uber_client_secret")
        if not secret or not signature:
            return False
        if isinstance(raw_body, str):
            raw_body = raw_body.encode()
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip().lower())

    # ------------------------------------------------------------------- auth
    @api.model
    def _get_token(self, force=False):
        icp = self.env["ir.config_parameter"].sudo()
        token = icp.get_param("thoughtocean_pos.uber_access_token")
        expiry = float(icp.get_param("thoughtocean_pos.uber_token_expiry", "0") or 0)
        if token and not force and expiry - 60 > time.time():
            return token
        client_id, secret = self._credentials()
        resp = requests.post(
            self._param("thoughtocean_pos.uber_auth_url"),
            data={
                "client_id": client_id,
                "client_secret": secret,
                "grant_type": "client_credentials",
                "scope": self._param("thoughtocean_pos.uber_scopes"),
            },
            timeout=TIMEOUT,
        )
        self._raise_for_status(resp, "token request")
        data = resp.json()
        token = data["access_token"]
        icp.set_param("thoughtocean_pos.uber_access_token", token)
        icp.set_param("thoughtocean_pos.uber_token_expiry", str(time.time() + int(data.get("expires_in", 2592000))))
        return token

    @api.model
    def _raise_for_status(self, resp, what):
        if resp.status_code >= 400:
            body = (resp.text or "")[:500]
            _logger.warning("Uber Eats API %s failed: HTTP %s %s", what, resp.status_code, body)
            raise UserError(_("Uber Eats API error on %(what)s: HTTP %(code)s %(body)s",
                              what=what, code=resp.status_code, body=body))

    @api.model
    def _request(self, method, path, payload=None, _retry=True):
        url = self._param("thoughtocean_pos.uber_api_base").rstrip("/") + path
        headers = {"Authorization": "Bearer " + self._get_token(), "Content-Type": "application/json"}
        resp = requests.request(method, url, headers=headers, json=payload, timeout=TIMEOUT)
        if resp.status_code == 401 and _retry:
            # token revoked or expired early: refresh once and retry
            self._get_token(force=True)
            return self._request(method, path, payload, _retry=False)
        self._raise_for_status(resp, f"{method} {path}")
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------------ calls
    @api.model
    def get_order(self, order_id):
        path = self._param("thoughtocean_pos.uber_get_order_path").format(order_id=order_id)
        return self._request("GET", path)

    @api.model
    def accept_order(self, order_id, external_reference):
        path = self._param("thoughtocean_pos.uber_accept_path").format(order_id=order_id)
        return self._request("POST", path, {"reason": "accepted", "external_reference_id": external_reference})

    @api.model
    def deny_order(self, order_id, explanation, code="OTHER"):
        path = self._param("thoughtocean_pos.uber_deny_path").format(order_id=order_id)
        return self._request("POST", path, {"reason": {"explanation": explanation, "code": code}})
