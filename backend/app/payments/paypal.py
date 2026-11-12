"""Async client for the PayPal REST API (OAuth, Orders v2, webhook verification).

- Amounts are Decimal and sent as exact strings ("11890.00").
- Every create/capture call carries a PayPal-Request-Id: repeating it returns PayPal's result for
  the earlier call instead of creating or capturing twice.
- Network timeouts raise PayPalTimeout, so callers know the outcome is *unknown* (not failed) and
  must re-read the order before deciding anything.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import Settings, get_settings


class PayPalError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"PayPal API error {status}: {body}")
        self.status = status
        self.body = body

    @property
    def issue(self) -> str | None:
        """PayPal's machine-readable issue code, e.g. INSTRUMENT_DECLINED."""
        if isinstance(self.body, dict):
            details = self.body.get("details") or []
            if details and isinstance(details[0], dict):
                return details[0].get("issue")
            return self.body.get("name")
        return None


class PayPalTimeout(RuntimeError):
    """The request may or may not have reached PayPal. Re-read before deciding."""


class PayPalNotConfigured(RuntimeError):
    pass


class PayPalClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.s = settings or get_settings()
        self._transport = transport
        self._token: str | None = None
        self._token_exp: float = 0

    @property
    def configured(self) -> bool:
        return bool(self.s.paypal_client_id and self.s.paypal_client_secret)

    @property
    def base_url(self) -> str:
        return self.s.paypal_base_url

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout or self.s.paypal_timeout_s, transport=self._transport)

    async def _access_token(self) -> str:
        if not self.configured:
            raise PayPalNotConfigured("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        try:
            async with self._client(timeout=15) as c:
                r = await c.post("/v1/oauth2/token", auth=(self.s.paypal_client_id, self.s.paypal_client_secret),
                                 data={"grant_type": "client_credentials"})
        except httpx.TimeoutException as e:
            raise PayPalTimeout("PayPal OAuth timed out") from e
        if r.status_code != 200:
            raise PayPalError(r.status_code, _body(r))
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + body.get("expires_in", 300)
        return self._token

    async def _request(self, method: str, path: str, *, json_body: Any = None, content: bytes | None = None,
                       request_id: str | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        token = await self._access_token()
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": "return=representation", **(headers or {})}
        if request_id:
            h["PayPal-Request-Id"] = request_id
        try:
            async with self._client() as c:
                r = await c.request(method, path, json=json_body, content=content, headers=h)
        except httpx.TimeoutException as e:
            raise PayPalTimeout(f"{method} {path} timed out") from e
        if r.status_code == 401:  # token revoked or expired early: drop it so the next call re-authenticates
            self._token = None
        if r.status_code >= 400:
            raise PayPalError(r.status_code, _body(r))
        return r.json() if r.content else {}

    # ---------- Orders v2 ----------

    async def create_order(self, *, amount: Decimal, currency: str, reference_id: str, description: str,
                           invoice_id: str | None = None, request_id: str) -> dict[str, Any]:
        unit: dict[str, Any] = {
            "reference_id": reference_id[:256],
            "description": description[:127],
            "amount": {"currency_code": currency, "value": f"{Decimal(amount):.2f}"},
        }
        if invoice_id:
            unit["invoice_id"] = invoice_id[:127]
        return await self._request("POST", "/v2/checkout/orders", json_body={"intent": "CAPTURE", "purchase_units": [unit]},
                                   request_id=request_id)

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")

    async def capture_order(self, order_id: str, *, request_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", content=b"{}", request_id=request_id)

    # ---------- webhooks ----------

    async def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> str:
        """Ask PayPal whether this delivery is genuine. Returns verification_status ("SUCCESS" if genuine).

        The event is embedded byte-for-byte (not parsed and re-serialised) — PayPal's docs warn that
        re-serialising can break verification.
        """
        if not self.s.paypal_webhook_id:
            raise PayPalNotConfigured("PAYPAL_WEBHOOK_ID is not set")
        h = {k.lower(): v for k, v in headers.items()}
        fields = {
            "auth_algo": h.get("paypal-auth-algo", ""),
            "cert_url": h.get("paypal-cert-url", ""),
            "transmission_id": h.get("paypal-transmission-id", ""),
            "transmission_sig": h.get("paypal-transmission-sig", ""),
            "transmission_time": h.get("paypal-transmission-time", ""),
            "webhook_id": self.s.paypal_webhook_id,
        }
        if not all(fields.values()):
            return "MISSING_HEADERS"
        prefix = json.dumps(fields)[:-1]  # drop the closing brace
        body = f'{prefix}, "webhook_event": '.encode() + raw_body.strip() + b"}"
        res = await self._request("POST", "/v1/notifications/verify-webhook-signature", content=body)
        return str(res.get("verification_status", "UNKNOWN"))


def _body(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return r.text


# ---------- helpers for reading PayPal responses ----------


def captures_of(order: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for unit in order.get("purchase_units") or []:
        out.extend((unit.get("payments") or {}).get("captures") or [])
    return out


def order_amount(order: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    units = order.get("purchase_units") or []
    amt = units[0].get("amount") if units else None
    return (Decimal(amt["value"]), amt["currency_code"]) if amt else (None, None)


def approve_link(order: dict[str, Any]) -> str | None:
    return next((link["href"] for link in order.get("links") or [] if link.get("rel") in ("approve", "payer-action")), None)


paypal = PayPalClient()


def get_client() -> PayPalClient:
    """The process-wide PayPal REST client (sandbox or live). Tests swap `app.payments.paypal.paypal` for a fake."""
    return paypal
