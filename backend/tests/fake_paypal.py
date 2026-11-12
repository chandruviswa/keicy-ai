"""Test-only in-memory PayPal Orders v2 fake. The app always talks to the real PayPal REST API.

Models what matters for safety: the buyer must approve before capture, PayPal-Request-Id makes
create/capture idempotent, and capture can be declined, time out, stay PENDING, or report a
different amount/currency than ordered. It never talks to PayPal and moves no money.
"""

import itertools
import uuid
from collections import Counter
from decimal import Decimal

from app.payments.paypal import PayPalError, PayPalNotConfigured, PayPalTimeout


class FakePayPal:
    def __init__(self) -> None:
        self.configured = True
        self.orders: dict[str, dict] = {}
        self.by_request_id: dict[str, dict] = {}
        self.calls: Counter = Counter()
        self._ids = itertools.count(1)
        # Behaviour knobs
        self.capture_mode = "complete"  # complete | decline | pending | timeout | timeout_after_capture
        self.capture_amount_override: str | None = None
        self.capture_currency_override: str | None = None
        self.get_order_fails = False
        self.live_amount_override: str | None = None  # what get_order reports later (reconciliation discrepancy)
        self.verify_result = "SUCCESS"
        self.webhook_configured = True

    # --- test helpers ---
    def approve(self, order_id: str) -> None:
        """What the buyer does in the PayPal popup."""
        self.orders[order_id]["status"] = "APPROVED"

    def complete_pending(self, order_id: str) -> None:
        for c in self._captures(order_id):
            c["status"] = "COMPLETED"
        self.orders[order_id]["status"] = "COMPLETED"

    def _captures(self, order_id: str) -> list[dict]:
        return self.orders[order_id]["purchase_units"][0].setdefault("payments", {}).setdefault("captures", [])

    # --- PayPalClient interface ---
    async def create_order(self, *, amount: Decimal, currency: str, reference_id: str, description: str, invoice_id=None, request_id: str):
        self.calls["create"] += 1
        if request_id in self.by_request_id:
            return self.by_request_id[request_id]
        oid = f"FAKE-{next(self._ids)}-{uuid.uuid4().hex[:10].upper()}"
        order = {
            "id": oid, "status": "CREATED", "intent": "CAPTURE",
            "purchase_units": [{"reference_id": reference_id, "amount": {"currency_code": currency, "value": f"{amount:.2f}"}}],
            "links": [{"rel": "approve", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}],
        }
        self.orders[oid] = order
        self.by_request_id[request_id] = order
        return order

    async def get_order(self, order_id: str):
        self.calls["get"] += 1
        if self.get_order_fails:
            raise PayPalTimeout("simulated GET timeout")
        order = self.orders[order_id]
        if self.live_amount_override:
            for c in self._captures(order_id):
                c["amount"]["value"] = self.live_amount_override
        return order

    async def capture_order(self, order_id: str, *, request_id: str):
        self.calls["capture"] += 1
        order = self.orders[order_id]
        if order["status"] == "COMPLETED" or self._captures(order_id):
            return order  # PayPal-Request-Id replay / already captured
        if order["status"] != "APPROVED":
            raise PayPalError(422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]})
        if self.capture_mode == "decline":
            raise PayPalError(422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]})
        if self.capture_mode == "timeout":
            raise PayPalTimeout("simulated capture timeout (nothing captured)")
        unit = order["purchase_units"][0]
        status = "PENDING" if self.capture_mode == "pending" else "COMPLETED"
        self._captures(order_id).append({
            "id": f"CAP-{order_id}", "status": status, "create_time": "2026-11-12T10:00:00Z",
            "amount": {"currency_code": self.capture_currency_override or unit["amount"]["currency_code"],
                       "value": self.capture_amount_override or unit["amount"]["value"]},
        })
        order["status"] = "COMPLETED"
        if self.capture_mode == "timeout_after_capture":
            raise PayPalTimeout("simulated timeout after PayPal captured")
        return order

    async def verify_webhook(self, headers, raw_body: bytes) -> str:
        self.calls["verify"] += 1
        if not self.webhook_configured:
            raise PayPalNotConfigured("PAYPAL_WEBHOOK_ID is not set")
        return self.verify_result
