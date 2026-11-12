"""Payments: create-order, capture, settlement, reconciliation, buyer cancel, timeouts, webhooks, recovery."""

import json
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

import app.payments.paypal as paypal_module
from app.agents.pipeline import executor, register_pipeline_resumers
from app.db.session import SessionLocal
from app.models import (
    Approval, AuditLog, Payment, PayPalCapture, ProcurementRequest, Product, PurchaseOrder, PurchaseOrderItem, User, Vendor, VendorOffer,
    WebhookEvent, WorkflowEvent,
)
from app.workflow import recover_interrupted
from app.workflow.recovery import clear_resumers
from seed.data import DEMO_USER_EMAIL
from tests.fake_paypal import FakePayPal



@pytest.fixture
def pp(monkeypatch) -> FakePayPal:
    fake = FakePayPal()
    monkeypatch.setattr(paypal_module, "paypal", fake)  # API deps, service and tools all read this
    return fake


async def approved_po(db, qty: int = 3, status: str = "APPROVED", approve: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """A request in APPROVED state with an APPROVED PO (3 × keyboards @ 69.00 = 207.00)."""
    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    approver = await db.scalar(select(User).where(User.role == "approver"))
    offer = await db.scalar(select(VendorOffer).join(Vendor).join(Product, Product.id == VendorOffer.product_id)
                            .where(Vendor.name == "Fabrikam Office", Product.sku == "KB-WL"))
    pr = ProcurementRequest(requester_id=user.id, raw_text="3 keyboards", budget=Decimal("1000"), currency="USD")
    db.add(pr)
    await db.flush()
    total = offer.unit_price * qty
    po = PurchaseOrder(po_number=f"PO-T-{uuid.uuid4().hex[:8].upper()}", request_id=pr.id, vendor_id=offer.vendor_id, status=status,
                       currency="USD", subtotal=total, tax_amount=Decimal("0.00"), total=total)
    db.add(po)
    await db.flush()
    db.add(PurchaseOrderItem(purchase_order_id=po.id, offer_id=offer.id, product_id=offer.product_id, description="item",
                             quantity=qty, unit_price=offer.unit_price, line_total=total))
    if approve:
        db.add(Approval(purchase_order_id=po.id, approver_id=approver.id, decision="APPROVED"))
    await db.commit()
    # Put the request where the workflow would have it (direct SQL: test setup only).
    await db.execute(text("UPDATE procurement_requests SET status = :s WHERE id = :id"),
                     {"s": "APPROVED" if approve else "AWAITING_APPROVAL", "id": pr.id})
    await db.commit()
    return pr.id, po.id


async def status_of(rid) -> str:
    async with SessionLocal() as s:
        return await s.scalar(select(ProcurementRequest.status).where(ProcurementRequest.id == rid))


async def create(client, po_id, **extra):
    return await client.post("/api/v1/payments/create-order", json={"purchase_order_id": str(po_id), **extra})


async def checkout(client, pp, po_id, **capture_extra):
    """Create order → buyer approves at PayPal → capture."""
    r = await create(client, po_id)
    assert r.status_code == 200, r.text
    order_id = r.json()["orderId"]
    pp.approve(order_id)
    cap = await client.post("/api/v1/payments/capture", json={"paypal_order_id": order_id, **capture_extra})
    return order_id, cap


# ---------- create-order ----------

async def test_create_order_requires_human_approval(client: AsyncClient, db, pp):
    _, po_id = await approved_po(db, status="PENDING_APPROVAL", approve=False)
    r = await create(client, po_id)
    assert r.status_code == 409 and "must be approved" in r.json()["detail"]
    assert pp.calls["create"] == 0


async def test_create_order_uses_server_amount_and_is_idempotent(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    assert (await create(client, po_id, amount="0.01")).status_code == 422  # the browser may not send an amount

    first = (await create(client, po_id)).json()
    assert first["amount"] == "207.00" and first["currency"] == "USD" and first["reused"] is False
    assert pp.orders[first["orderId"]]["purchase_units"][0]["amount"]["value"] == "207.00"
    assert await status_of(rid) == "PAYMENT_PENDING"

    again = (await create(client, po_id)).json()
    assert again["orderId"] == first["orderId"] and again["reused"] is True
    assert pp.calls["create"] == 1


async def test_only_the_requester_can_pay(client: AsyncClient, db, pp, approver):
    _, po_id = await approved_po(db)
    r = await client.post("/api/v1/payments/create-order", json={"purchase_order_id": str(po_id)}, headers=approver)
    assert r.status_code == 404


# ---------- capture → reconcile ----------

async def test_full_checkout_captures_and_reconciles(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    order_id, cap = await checkout(client, pp, po_id)
    assert cap.status_code == 200, cap.text
    assert cap.json()["status"] == "COMPLETED" and cap.json()["capture_id"] == f"CAP-{order_id}"

    await executor.wait(rid)  # reconciliation runs in the background
    assert await status_of(rid) == "COMPLETED"
    db.expire_all()
    po = await db.get(PurchaseOrder, po_id)
    assert po.status == "PAID"
    pay = (await client.get("/api/v1/payments", params={"request_id": str(rid)})).json()[0]
    assert pay["status"] == "COMPLETED" and pay["reconciled_at"]
    assert pay["receipt"]["amount"] == "207.00" and pay["receipt"]["capture_id"] == f"CAP-{order_id}"
    assert pay["receipt"]["receipt_number"].startswith("RCPT-") and pay["captures"][0]["capture_id"] == f"CAP-{order_id}"
    assert (await client.get(f"/api/v1/payments/{pay['id']}")).json()["id"] == pay["id"]

    types = [e.event_type for e in (await db.scalars(select(WorkflowEvent).where(WorkflowEvent.request_id == rid).order_by(WorkflowEvent.seq))).all()]
    assert "payment.order_created" in types and "payment.completed" in types and types[-1] == "workflow.completed"


async def test_duplicate_capture_is_harmless(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    order_id, _ = await checkout(client, pp, po_id)
    await executor.wait(rid)
    again = await client.post("/api/v1/payments/capture", json={"paypal_order_id": order_id})
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert pp.calls["capture"] == 1
    assert await db.scalar(select(text("count(*)")).select_from(PayPalCapture).where(PayPalCapture.capture_id == f"CAP-{order_id}")) == 1


async def test_declined_then_retry_with_new_order(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.capture_mode = "decline"
    order_id, cap = await checkout(client, pp, po_id)
    pp.capture_mode = "complete"
    assert cap.json()["status"] == "FAILED" and "INSTRUMENT_DECLINED" in cap.json()["message"]
    assert await status_of(rid) == "PAYMENT_FAILED"

    order2, cap2 = await checkout(client, pp, po_id)
    assert order2 != order_id and cap2.json()["status"] == "COMPLETED"
    pays = (await client.get("/api/v1/payments", params={"request_id": str(rid)})).json()
    assert [(p["attempt"], p["status"]) for p in pays] == [(1, "FAILED"), (2, "COMPLETED")]
    await executor.wait(rid)


async def test_capture_without_buyer_approval_fails(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    order_id = (await create(client, po_id)).json()["orderId"]
    cap = await client.post("/api/v1/payments/capture", json={"paypal_order_id": order_id})  # buyer never approved
    assert cap.json()["status"] == "FAILED" and "ORDER_NOT_APPROVED" in cap.json()["message"]


@pytest.mark.parametrize("knob,value,issue", [
    ("capture_amount_override", "1.00", "captured 1.00 but the purchase order total is 207.00"),
    ("capture_currency_override", "EUR", "captured in EUR but the purchase order is in USD"),
])
async def test_wrong_amount_or_currency_is_not_treated_as_paid(client: AsyncClient, db, pp, knob, value, issue):
    rid, po_id = await approved_po(db)
    setattr(pp, knob, value)
    _, cap = await checkout(client, pp, po_id)
    body = cap.json()
    assert body["status"] == "FAILED" and issue in body["issues"]
    assert await status_of(rid) == "PAYMENT_FAILED"
    assert (await db.get(PurchaseOrder, po_id)).status == "APPROVED"  # never PAID
    pay_id = uuid.UUID(body["payment_id"])
    assert await db.scalar(select(AuditLog.id).where(AuditLog.entity_id == pay_id, AuditLog.action == "payment.discrepancy"))


async def test_timeout_after_paypal_captured_is_resolved_by_lookup(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.capture_mode = "timeout_after_capture"
    _, cap = await checkout(client, pp, po_id)
    assert cap.status_code == 200 and cap.json()["status"] == "COMPLETED"
    assert pp.calls["get"] >= 1
    await executor.wait(rid)
    assert await status_of(rid) == "COMPLETED"


async def test_unknown_outcome_stays_processing_until_recovery(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.capture_mode, pp.get_order_fails = "timeout", True
    order_id, cap = await checkout(client, pp, po_id)
    assert cap.status_code == 202 and cap.json()["status"] == "PROCESSING"
    assert await status_of(rid) == "PAYMENT_PROCESSING"

    # Later (e.g. after a restart) PayPal is reachable again; recovery asks PayPal and finishes the job.
    pp.capture_mode, pp.get_order_fails = "complete", False
    clear_resumers()
    register_pipeline_resumers()
    try:
        report = await recover_interrupted(SessionLocal)
    finally:
        clear_resumers()
    assert rid in report.resumed
    await executor.wait(rid)
    assert await status_of(rid) == "COMPLETED"


async def test_buyer_cancel_keeps_request_payable(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    order_id = (await create(client, po_id)).json()["orderId"]
    r = await client.post("/api/v1/payments/cancel", json={"paypal_order_id": order_id})
    assert r.json()["status"] == "CANCELLED" and await status_of(rid) == "PAYMENT_PENDING"
    assert (await client.post("/api/v1/payments/capture", json={"paypal_order_id": order_id})).status_code == 409
    new = (await create(client, po_id)).json()
    assert new["orderId"] != order_id and new["reused"] is False


async def test_reconciliation_discrepancy_fails_request(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.live_amount_override = "200.00"  # PayPal's live record later disagrees with what we stored
    _, cap = await checkout(client, pp, po_id)
    assert cap.json()["status"] == "COMPLETED"
    await executor.wait(rid)
    assert await status_of(rid) == "FAILED"
    db.expire_all()
    assert (await db.get(PurchaseOrder, po_id)).status == "APPROVED"  # not PAID


# ---------- webhooks ----------

def capture_event(order_id: str, event_type="PAYMENT.CAPTURE.COMPLETED", event_id=None) -> bytes:
    return json.dumps({"id": event_id or f"WH-{uuid.uuid4().hex}", "event_type": event_type, "resource": {
        "id": f"CAP-{order_id}", "status": "COMPLETED", "supplementary_data": {"related_ids": {"order_id": order_id}}}}).encode()


async def post_webhook(client, body: bytes):
    return await client.post("/api/v1/payments/webhook", content=body, headers={"Content-Type": "application/json"})


async def test_webhook_completes_a_pending_capture(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.capture_mode = "pending"
    order_id, cap = await checkout(client, pp, po_id)
    assert cap.status_code == 202 and await status_of(rid) == "PAYMENT_PROCESSING"

    pp.complete_pending(order_id)  # PayPal finishes processing and notifies us
    r = await post_webhook(client, capture_event(order_id))
    assert r.status_code == 200 and r.json()["status"] == "PROCESSED"
    await executor.wait(rid)
    assert await status_of(rid) == "COMPLETED"


async def test_webhook_with_bad_signature_is_rejected(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.capture_mode = "pending"
    order_id, _ = await checkout(client, pp, po_id)
    pp.complete_pending(order_id)
    pp.verify_result = "FAILURE"
    body = capture_event(order_id)
    r = await post_webhook(client, body)
    assert r.status_code == 400
    assert await status_of(rid) == "PAYMENT_PROCESSING"  # nothing trusted
    row = await db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == json.loads(body)["id"]))
    assert (row.status, row.verification) == ("REJECTED", "FAILURE")


async def test_duplicate_webhook_is_processed_once(client: AsyncClient, db, pp):
    rid, po_id = await approved_po(db)
    pp.capture_mode = "pending"
    order_id, _ = await checkout(client, pp, po_id)
    pp.complete_pending(order_id)
    body = capture_event(order_id, event_id=f"WH-DUP-{uuid.uuid4().hex}")
    assert (await post_webhook(client, body)).json()["status"] == "PROCESSED"
    assert (await post_webhook(client, body)).json()["status"] == "duplicate"
    assert pp.calls["verify"] == 1
    await executor.wait(rid)


async def test_webhook_edge_cases(client: AsyncClient, pp):
    assert (await post_webhook(client, b"not json")).status_code == 400
    r = await post_webhook(client, capture_event("UNKNOWN-ORDER"))
    assert r.status_code == 200 and r.json()["status"] == "IGNORED"
    pp.webhook_configured = False
    assert (await post_webhook(client, capture_event("UNKNOWN-ORDER"))).status_code == 503


async def test_payment_config_exposes_no_secret(client: AsyncClient):
    cfg = (await client.get("/api/v1/payments/config")).json()
    assert cfg["sdk_url"].endswith("/web-sdk/v6/core")
    assert "secret" not in json.dumps(cfg).lower()
