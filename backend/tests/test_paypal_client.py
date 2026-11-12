"""The real PayPal client against mocked HTTP: headers, amounts, timeouts, errors, webhook verification."""

import json
from decimal import Decimal

import httpx
import pytest

from app.payments.paypal import PayPalClient, PayPalError, PayPalNotConfigured, PayPalTimeout, approve_link, captures_of, order_amount
from tests.fakes import test_settings

S = test_settings(paypal_client_id="cid", paypal_client_secret="secret", paypal_mode="sandbox", paypal_webhook_id="WH-123")
TOKEN = {"access_token": "tok", "expires_in": 3600}


def client(handler) -> tuple[PayPalClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrap(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return handler(req)
    return PayPalClient(S, transport=httpx.MockTransport(wrap)), seen


async def test_create_order_sends_exact_amount_and_request_id():
    c, seen = client(lambda r: httpx.Response(201, json={"id": "O1", "status": "CREATED", "purchase_units": [{"amount": {"currency_code": "USD", "value": "11890.00"}}],
                                                         "links": [{"rel": "payer-action", "href": "https://x/approve"}]}))
    o = await c.create_order(amount=Decimal("11890"), currency="USD", reference_id="po", description="d", invoice_id="PO-1-1", request_id="req-1")
    req = seen[-1]
    body = json.loads(req.content)
    assert str(req.url) == "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    assert req.headers["PayPal-Request-Id"] == "req-1" and req.headers["Authorization"] == "Bearer tok"
    assert body["intent"] == "CAPTURE" and body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "11890.00"}
    assert order_amount(o) == (Decimal("11890.00"), "USD") and approve_link(o) == "https://x/approve"
    assert seen[0].headers["Authorization"].startswith("Basic ")  # client secret only goes to the token endpoint


async def test_capture_is_idempotent_and_never_forces_a_mock_response():
    c, seen = client(lambda r: httpx.Response(201, json={"id": "O1", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [{"id": "C1"}]}}]}))
    o = await c.capture_order("O1", request_id="capture-O1")
    assert "PayPal-Mock-Response" not in seen[-1].headers
    assert seen[-1].headers["PayPal-Request-Id"] == "capture-O1" and captures_of(o) == [{"id": "C1"}]


async def test_errors_expose_issue_code():
    c, _ = client(lambda r: httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INSTRUMENT_DECLINED"}]}))
    with pytest.raises(PayPalError) as e:
        await c.capture_order("O1", request_id="x")
    assert (e.value.status, e.value.issue) == (422, "INSTRUMENT_DECLINED")


async def test_timeout_is_distinct_from_failure():
    def boom(r):
        raise httpx.ReadTimeout("slow", request=r)
    c, _ = client(boom)
    with pytest.raises(PayPalTimeout):
        await c.capture_order("O1", request_id="x")


async def test_token_is_cached():
    c, seen = client(lambda r: httpx.Response(200, json={"id": "O1"}))
    await c.get_order("O1")
    await c.get_order("O1")
    assert sum(1 for r in seen if r.url.path == "/v1/oauth2/token") == 1


async def test_not_configured():
    c = PayPalClient(test_settings(paypal_client_id="", paypal_client_secret=""))
    with pytest.raises(PayPalNotConfigured):
        await c.get_order("O1")


HEADERS = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.paypal.com/cert", "PAYPAL-TRANSMISSION-ID": "t1",
           "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "2026-11-12T10:00:00Z"}


async def test_verify_webhook_posts_raw_event_verbatim():
    c, seen = client(lambda r: httpx.Response(200, json={"verification_status": "SUCCESS"}))
    raw = b'{"id":"WH-1",  "event_type":"PAYMENT.CAPTURE.COMPLETED","resource":{"amount":{"value":"1.10"}}}'
    assert await c.verify_webhook({k.lower(): v for k, v in HEADERS.items()}, raw) == "SUCCESS"
    sent = seen[-1].content
    assert raw in sent  # byte-for-byte, original spacing kept
    body = json.loads(sent)
    assert body["webhook_id"] == "WH-123" and body["transmission_sig"] == "sig" and body["webhook_event"]["id"] == "WH-1"


async def test_verify_webhook_without_headers_or_id():
    c, seen = client(lambda r: httpx.Response(200, json={"verification_status": "SUCCESS"}))
    assert await c.verify_webhook({}, b"{}") == "MISSING_HEADERS"
    assert not any(r.url.path.endswith("verify-webhook-signature") for r in seen)
    with pytest.raises(PayPalNotConfigured):
        await PayPalClient(test_settings(paypal_client_id="a", paypal_client_secret="b", paypal_webhook_id="")).verify_webhook(HEADERS, b"{}")
