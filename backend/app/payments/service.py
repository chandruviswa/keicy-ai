"""Payment flow: PayPal order creation, capture, settlement, buyer cancel, webhooks, recovery.

Rules this module enforces:
  * the amount always comes from the purchase order in our DB (re-added from its lines), never the browser;
  * a PO must be APPROVED by a human (recorded Approval) before an order is created or captured;
  * only the requester (or an admin) can pay for a request;
  * create-order is idempotent per checkout attempt; capture is idempotent per PayPal order
    (row lock + PayPal-Request-Id + UNIQUE capture_id);
  * "paid" is decided only from PayPal's own capture data, after checking status, amount and currency;
  * a timeout means "unknown": we re-read the order from PayPal instead of guessing.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.models import AuditLog, Payment, PayPalCapture, PayPalOrder, ProcurementRequest, PurchaseOrder, User, WebhookEvent
from app.models.enums import ActorType, PaymentStatus as P, PurchaseOrderStatus as PO, UserRole, WebhookStatus, WorkflowState as S
from app.payments.paypal import PayPalClient, PayPalError, PayPalNotConfigured, PayPalTimeout, captures_of, get_client, order_amount
from app.tools.common import money
from app.workflow import Actor, has_approval, transition
from app.workflow.events import emit

log = logging.getLogger("procura.payments")

OPEN_PAYPAL_STATUSES = {"CREATED", "SAVED", "APPROVED", "PAYER_ACTION_REQUIRED"}
DECLINED_CAPTURE = {"DECLINED", "DENIED", "FAILED"}


class PaymentError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code, self.message = status_code, message


@dataclass
class CheckoutOrder:
    payment_id: uuid.UUID
    paypal_order_id: str
    status: str
    amount: Decimal
    currency: str
    reused: bool


@dataclass
class CaptureOutcome:
    payment_id: uuid.UUID
    status: str  # our Payment status: COMPLETED | PROCESSING | FAILED
    request_status: str
    capture_id: str | None = None
    message: str | None = None
    duplicate: bool = False
    issues: list[str] = field(default_factory=list)


def server_total(po: PurchaseOrder) -> Decimal:
    """Recompute the PO total from its lines. Raises if the stored numbers don't add up."""
    for item in po.items:
        if item.line_total != money(item.unit_price * item.quantity):
            raise PaymentError(409, f"Line '{item.description}' total does not match unit price × quantity")
    total = money(sum((i.line_total for i in po.items), Decimal("0"))) + po.tax_amount
    if not po.items or total != po.total:
        raise PaymentError(409, f"Purchase order total {po.total} does not match its lines ({total})")
    return total


class PaymentService:
    def __init__(self, db: AsyncSession, client: PayPalClient | None = None) -> None:
        self.db = db
        self.paypal = client or get_client()

    # ------------------------------------------------------------------ create order

    async def create_order(self, po_id: uuid.UUID, user: User) -> CheckoutOrder:
        po = await self.db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == po_id)
                                  .options(selectinload(PurchaseOrder.items)).with_for_update(of=PurchaseOrder))
        pr = await self.db.get(ProcurementRequest, po.request_id) if po else None
        self._check_owner(pr, user, "Purchase order not found")
        if po.status != PO.APPROVED or not await has_approval(self.db, pr.id):
            raise PaymentError(409, f"Purchase order {po.po_number} is {po.status}; it must be approved before payment")
        if pr.status not in (S.APPROVED, S.PAYMENT_PENDING, S.PAYMENT_FAILED):
            raise PaymentError(409, f"Request is {pr.status}; payment can't be started now")
        amount = server_total(po)

        payments = (await self.db.scalars(select(Payment).where(Payment.purchase_order_id == po.id).order_by(Payment.attempt))).all()
        if any(p.status == P.COMPLETED for p in payments):
            raise PaymentError(409, "This purchase order has already been paid")
        if any(p.status == P.PROCESSING for p in payments):
            raise PaymentError(409, "A capture for this purchase order is already in progress")

        # Duplicate create-order: hand back the still-open PayPal order instead of creating another.
        for p in reversed(payments):
            if p.status in (P.CREATED, P.PENDING):
                existing = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.payment_id == p.id).order_by(PayPalOrder.created_at.desc()).limit(1))
                if existing and existing.status in OPEN_PAYPAL_STATUSES and existing.amount == amount:
                    return CheckoutOrder(p.id, existing.paypal_order_id, existing.status, existing.amount, existing.currency, reused=True)
                if existing is None:
                    payment = p  # prepared (e.g. by the finance tool) but no PayPal order yet
                    break
                p.status, p.failure_reason = P.CANCELLED, "superseded by a new checkout attempt"
        else:
            payment = None

        if payment is None:
            attempt = (max((p.attempt for p in payments), default=0)) + 1
            payment = Payment(purchase_order_id=po.id, amount=amount, currency=po.currency, status=P.CREATED,
                              attempt=attempt, idempotency_key=f"po-{po.id}-attempt-{attempt}")
            self.db.add(payment)
            await self.db.flush()

        try:
            order = await self.paypal.create_order(
                amount=amount, currency=po.currency, reference_id=str(po.id), description=f"Procura {po.po_number}",
                invoice_id=f"{po.po_number}-{payment.attempt}", request_id=payment.idempotency_key,
            )
        except PayPalNotConfigured as e:
            await self.db.rollback()
            raise PaymentError(503, str(e)) from e
        except PayPalTimeout as e:
            # Nothing saved; retrying uses the same PayPal-Request-Id, so PayPal returns the same order if it was created.
            await self.db.rollback()
            raise PaymentError(504, "PayPal did not respond in time — please try again") from e
        except PayPalError as e:
            await self.db.rollback()
            raise PaymentError(502, f"PayPal rejected the order: {e.issue or e.status}") from e

        replay = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.paypal_order_id == order["id"]))
        if replay is not None:  # PayPal answered an idempotent replay with an order we already stored
            await self.db.rollback()
            return CheckoutOrder(replay.payment_id, replay.paypal_order_id, replay.status, replay.amount, replay.currency, reused=True)

        paid_amount, paid_currency = order_amount(order)
        if paid_amount != amount or paid_currency != po.currency:
            await self.db.rollback()
            raise PaymentError(502, f"PayPal order amount {paid_amount} {paid_currency} does not match {amount} {po.currency}")

        self.db.add(PayPalOrder(payment_id=payment.id, paypal_order_id=order["id"], status=order.get("status", "CREATED"),
                                amount=amount, currency=po.currency, raw=order))
        payment.status = P.PENDING
        if pr.status != S.PAYMENT_PENDING:
            await transition(self.db, pr.id, S.PAYMENT_PENDING, actor=Actor.user(user.id), reason="PayPal checkout started",
                             payload={"paypal_order_id": order["id"], "amount": str(amount)}, commit=False)
        await emit(self.db, pr.id, "payment.order_created", {"paypal_order_id": order["id"], "amount": str(amount), "currency": po.currency, "attempt": payment.attempt})
        self._audit(user, "payment.order_created", "payment", payment.id, {"paypal_order_id": order["id"], "amount": str(amount)})
        try:
            await self.db.commit()
        except IntegrityError:  # PayPal returned an order we already stored (idempotent replay)
            await self.db.rollback()
            existing = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.paypal_order_id == order["id"]))
            return CheckoutOrder(existing.payment_id, existing.paypal_order_id, existing.status, existing.amount, existing.currency, reused=True)
        return CheckoutOrder(payment.id, order["id"], order.get("status", "CREATED"), amount, po.currency, reused=False)

    # ------------------------------------------------------------------ capture

    async def capture(self, paypal_order_id: str, user: User, *, mock_response: str | None = None) -> CaptureOutcome:
        ppo, payment, po, pr = await self._load_by_order(paypal_order_id, lock=True)
        self._check_owner(pr, user, "PayPal order not found")

        if payment.status == P.COMPLETED:
            cap = await self.db.scalar(select(PayPalCapture.capture_id).where(PayPalCapture.paypal_order_ref_id == ppo.id).limit(1))
            return CaptureOutcome(payment.id, payment.status, pr.status, capture_id=cap, duplicate=True, message="Already captured")
        if payment.status == P.PROCESSING:
            await self.db.commit()  # release the lock; another request may be mid-capture
            return await self.resolve(payment.id)
        if payment.status != P.PENDING:
            raise PaymentError(409, f"Payment is {payment.status}; start a new checkout")
        if po.status != PO.APPROVED or pr.status != S.PAYMENT_PENDING or not await has_approval(self.db, pr.id):
            raise PaymentError(409, "Purchase order is not approved for payment")
        server_total(po)  # the PO must still add up before money moves

        payment.status = P.PROCESSING
        await transition(self.db, pr.id, S.PAYMENT_PROCESSING, actor=Actor.user(user.id), reason="Capturing PayPal payment",
                         payload={"paypal_order_id": paypal_order_id}, commit=False)
        await self.db.commit()  # persist "processing" before talking to PayPal

        return await self._capture_and_settle(payment.id, paypal_order_id, mock_response=mock_response)

    async def _capture_and_settle(self, payment_id: uuid.UUID, paypal_order_id: str, *, mock_response: str | None = None) -> CaptureOutcome:
        request_id = f"capture-{paypal_order_id}"  # same id on every retry → PayPal never captures twice
        try:
            order = await self.paypal.capture_order(paypal_order_id, request_id=request_id, mock_response=mock_response)
        except PayPalTimeout:
            return await self.resolve(payment_id, retry_capture=False)
        except PayPalError as e:
            if e.issue == "ORDER_ALREADY_CAPTURED":
                return await self.resolve(payment_id, retry_capture=False)
            if e.status >= 500:
                return await self.resolve(payment_id, retry_capture=False)
            return await self._fail(payment_id, f"PayPal declined the capture: {e.issue or e.status}", e.body if isinstance(e.body, dict) else None)
        return await self.settle(payment_id, order, source="capture")

    async def resolve(self, payment_id: uuid.UUID, *, retry_capture: bool = True) -> CaptureOutcome:
        """Find out what really happened by reading the order from PayPal, then settle accordingly."""
        payment = await self.db.get(Payment, payment_id, populate_existing=True)
        ppo = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.payment_id == payment_id).order_by(PayPalOrder.created_at.desc()).limit(1))
        try:
            order = await self.paypal.get_order(ppo.paypal_order_id)
        except (PayPalTimeout, PayPalError) as e:
            pr = await self._request_of(payment)
            return CaptureOutcome(payment.id, payment.status, pr.status, message=f"PayPal status unknown ({e}); will retry")
        if captures_of(order):
            return await self.settle(payment_id, order, source="lookup")
        if order.get("status") == "APPROVED" and retry_capture and payment.status == P.PROCESSING:
            return await self._capture_and_settle(payment_id, ppo.paypal_order_id)
        pr = await self._request_of(payment)
        return CaptureOutcome(payment.id, payment.status, pr.status, message=f"PayPal order is {order.get('status')}; not captured yet")

    # ------------------------------------------------------------------ settle

    async def settle(self, payment_id: uuid.UUID, order: dict[str, Any], *, source: str) -> CaptureOutcome:
        """Apply PayPal's capture data to our records. Idempotent: settling twice changes nothing."""
        payment = await self.db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update().execution_options(populate_existing=True))
        ppo = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.payment_id == payment_id, PayPalOrder.paypal_order_id == order["id"]))
        po = await self.db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == payment.purchase_order_id).options(selectinload(PurchaseOrder.items)))
        pr = await self.db.get(ProcurementRequest, po.request_id, populate_existing=True)
        ppo.status, ppo.raw = order.get("status", ppo.status), order

        captures = captures_of(order)
        for c in captures:  # store every capture PayPal reports, once (capture_id is UNIQUE)
            if not await self.db.scalar(select(PayPalCapture.id).where(PayPalCapture.capture_id == c["id"])):
                self.db.add(PayPalCapture(paypal_order_ref_id=ppo.id, capture_id=c["id"], status=c["status"],
                                          amount=Decimal(c["amount"]["value"]), currency=c["amount"]["currency_code"], raw=c))
            else:
                existing = await self.db.scalar(select(PayPalCapture).where(PayPalCapture.capture_id == c["id"]))
                existing.status, existing.raw = c["status"], c

        if payment.status == P.COMPLETED:
            await self.db.commit()
            return CaptureOutcome(payment.id, payment.status, pr.status, capture_id=captures[0]["id"] if captures else None, duplicate=True)

        completed = [c for c in captures if c["status"] == "COMPLETED"]
        pending = [c for c in captures if c["status"] == "PENDING"]
        declined = [c for c in captures if c["status"] in DECLINED_CAPTURE]

        if completed:
            captured = money(sum((Decimal(c["amount"]["value"]) for c in completed), Decimal("0")))
            currencies = {c["amount"]["currency_code"] for c in completed}
            issues = []
            if captured != payment.amount or captured != po.total:
                issues.append(f"captured {captured} but the purchase order total is {po.total}")
            if currencies != {po.currency}:
                issues.append(f"captured in {', '.join(sorted(currencies))} but the purchase order is in {po.currency}")
            if issues:
                self._audit(None, "payment.discrepancy", "payment", payment.id, {"issues": issues, "paypal_order_id": order["id"]})
                await self.db.flush()
                return await self._fail(payment.id, "Payment needs manual review: " + "; ".join(issues), order, issues=issues)

            payment.status, payment.failure_reason = P.COMPLETED, None
            await self._ensure_processing(pr)
            await transition(self.db, pr.id, S.PAYMENT_COMPLETED, actor=Actor.system(f"paypal:{source}"),
                             reason=f"PayPal capture {completed[0]['id']} verified", payload={"capture_id": completed[0]["id"], "amount": str(captured)}, commit=False)
            await emit(self.db, pr.id, "payment.completed", {"capture_id": completed[0]["id"], "amount": str(captured), "currency": po.currency, "source": source})
            self._audit(None, "payment.completed", "payment", payment.id, {"capture_id": completed[0]["id"], "amount": str(captured), "source": source})
            await self.db.commit()
            _start_reconciliation(pr.id)
            return CaptureOutcome(payment.id, P.COMPLETED, S.PAYMENT_COMPLETED, capture_id=completed[0]["id"])

        if pending:
            await self._ensure_processing(pr)
            payment.status = P.PROCESSING
            await self.db.commit()
            return CaptureOutcome(payment.id, P.PROCESSING, S.PAYMENT_PROCESSING, capture_id=pending[0]["id"],
                                  message="PayPal is still processing this capture; it will complete automatically")

        if declined:
            return await self._fail(payment.id, f"PayPal capture {declined[0]['id']} was {declined[0]['status'].lower()}", order)

        await self.db.commit()
        return CaptureOutcome(payment.id, payment.status, pr.status, message=f"PayPal order is {order.get('status')}")

    async def _fail(self, payment_id: uuid.UUID, reason: str, raw: dict | None, issues: list[str] | None = None) -> CaptureOutcome:
        payment = await self.db.get(Payment, payment_id, populate_existing=True)
        pr = await self._request_of(payment)
        if payment.status == P.FAILED:  # already failed (e.g. a redelivered webhook)
            await self.db.commit()
            return CaptureOutcome(payment.id, P.FAILED, pr.status, message=payment.failure_reason, duplicate=True)
        payment.status, payment.failure_reason = P.FAILED, reason[:2000]
        await self._ensure_processing(pr)
        await transition(self.db, pr.id, S.PAYMENT_FAILED, actor=Actor.system("paypal"), reason=reason[:2000], commit=False)
        await emit(self.db, pr.id, "payment.failed", {"payment_id": str(payment.id), "reason": reason})
        self._audit(None, "payment.failed", "payment", payment.id, {"reason": reason})
        await self.db.commit()
        return CaptureOutcome(payment.id, P.FAILED, S.PAYMENT_FAILED, message=reason, issues=issues or [])

    async def _ensure_processing(self, pr: ProcurementRequest) -> None:
        """Webhooks/lookups may settle a payment whose request never reached PROCESSING (e.g. capture call crashed)."""
        await self.db.refresh(pr)
        if pr.status == S.PAYMENT_PENDING:
            await transition(self.db, pr.id, S.PAYMENT_PROCESSING, actor=Actor.system("paypal"), reason="Capture reported by PayPal", commit=False)

    # ------------------------------------------------------------------ buyer cancel

    async def cancel_checkout(self, paypal_order_id: str, user: User) -> CaptureOutcome:
        ppo, payment, po, pr = await self._load_by_order(paypal_order_id, lock=True)
        self._check_owner(pr, user, "PayPal order not found")
        if payment.status not in (P.CREATED, P.PENDING):
            raise PaymentError(409, f"Payment is {payment.status} and can't be cancelled")
        payment.status, payment.failure_reason = P.CANCELLED, "Buyer cancelled the PayPal checkout"
        await emit(self.db, pr.id, "payment.cancelled", {"paypal_order_id": paypal_order_id})
        self._audit(user, "payment.cancelled", "payment", payment.id, {"paypal_order_id": paypal_order_id})
        await self.db.commit()
        # The request stays PAYMENT_PENDING (or the PO approved) — the buyer can simply try again.
        return CaptureOutcome(payment.id, P.CANCELLED, pr.status, message="Checkout cancelled; you can pay again")

    # ------------------------------------------------------------------ webhooks

    async def handle_webhook(self, headers: dict[str, str], raw_body: bytes) -> tuple[int, dict[str, Any]]:
        try:
            event = json.loads(raw_body)
            event_id, event_type = event["id"], event["event_type"]
        except (ValueError, KeyError, TypeError):
            return 400, {"error": "malformed webhook body"}
        resource = event.get("resource") or {}

        if await self.db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_id == event_id)):
            return 200, {"status": "duplicate"}

        try:
            verification = await self.paypal.verify_webhook(headers, raw_body)
        except PayPalNotConfigured as e:
            return 503, {"error": str(e)}
        except (PayPalTimeout, PayPalError) as e:
            return 503, {"error": f"could not verify with PayPal: {e}"}  # PayPal will redeliver

        row = WebhookEvent(event_id=event_id, event_type=event_type, resource_id=resource.get("id"), verification=verification,
                           payload=event, status=WebhookStatus.REJECTED if verification != "SUCCESS" else WebhookStatus.IGNORED)
        self.db.add(row)
        try:
            await self.db.commit()  # claims the event id; a concurrent redelivery now fails here
        except IntegrityError:
            await self.db.rollback()
            return 200, {"status": "duplicate"}
        if verification != "SUCCESS":
            log.warning("Rejected PayPal webhook %s (%s): verification %s", event_id, event_type, verification)
            return 400, {"error": "signature verification failed"}

        row_id = row.id
        try:
            status, result = await self._process_event(event_type, resource)
        except Exception as e:  # noqa: BLE001
            await self.db.rollback()
            log.exception("Webhook %s processing failed", event_id)
            status, result = WebhookStatus.FAILED, f"{type(e).__name__}: {e}"
        row = await self.db.get(WebhookEvent, row_id, populate_existing=True)
        row.status, row.result, row.processed_at = status, result[:2000], datetime.now(timezone.utc)
        await self.db.commit()
        return 200, {"status": status, "result": result}

    async def _process_event(self, event_type: str, resource: dict[str, Any]) -> tuple[WebhookStatus, str]:
        if event_type.startswith("PAYMENT.CAPTURE."):
            order_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
            ppo = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.paypal_order_id == order_id)) if order_id else None
            if ppo is None:
                return WebhookStatus.IGNORED, f"no local order for {order_id}"
            if event_type in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
                self._audit(None, "payment.refunded" if event_type.endswith("REFUNDED") else "payment.reversed", "payment", ppo.payment_id,
                            {"capture_id": resource.get("id"), "amount": (resource.get("amount") or {}).get("value")})
                await self.db.commit()
                return WebhookStatus.PROCESSED, "recorded for finance review"
            # Never trust the webhook body for money: re-read the order from PayPal and settle from that.
            order = await self.paypal.get_order(order_id)
            outcome = await self.settle(ppo.payment_id, order, source="webhook")
            return WebhookStatus.PROCESSED, f"payment {outcome.status}"
        if event_type == "CHECKOUT.ORDER.APPROVED":
            ppo = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.paypal_order_id == resource.get("id")))
            if ppo is None:
                return WebhookStatus.IGNORED, "unknown order"
            if ppo.status in ("CREATED", "SAVED", "PAYER_ACTION_REQUIRED"):
                ppo.status = "APPROVED"
                await self.db.commit()
            return WebhookStatus.PROCESSED, "order marked approved"
        return WebhookStatus.IGNORED, f"{event_type} not handled"

    # ------------------------------------------------------------------ helpers

    async def _load_by_order(self, paypal_order_id: str, *, lock: bool):
        ppo = await self.db.scalar(select(PayPalOrder).where(PayPalOrder.paypal_order_id == paypal_order_id))
        if ppo is None:
            raise PaymentError(404, "PayPal order not found")
        stmt = select(Payment).where(Payment.id == ppo.payment_id).execution_options(populate_existing=True)
        payment = await self.db.scalar(stmt.with_for_update() if lock else stmt)
        po = await self.db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == payment.purchase_order_id).options(selectinload(PurchaseOrder.items)))
        pr = await self.db.get(ProcurementRequest, po.request_id, populate_existing=True)
        return ppo, payment, po, pr

    async def _request_of(self, payment: Payment) -> ProcurementRequest:
        po = await self.db.get(PurchaseOrder, payment.purchase_order_id)
        return await self.db.get(ProcurementRequest, po.request_id, populate_existing=True)

    @staticmethod
    def _check_owner(pr: ProcurementRequest | None, user: User, not_found: str) -> None:
        if pr is None or (pr.requester_id != user.id and user.role != UserRole.ADMIN):
            # 404 rather than 403 so payment ids can't be probed.
            raise PaymentError(404, not_found)

    def _audit(self, user: User | None, action: str, entity_type: str, entity_id, details: dict) -> None:
        self.db.add(AuditLog(actor_type=ActorType.USER if user else ActorType.SYSTEM, actor_user_id=user.id if user else None,
                             actor_name=None if user else "payments", action=action, entity_type=entity_type, entity_id=entity_id, details=details))


def _start_reconciliation(request_id: uuid.UUID) -> None:
    from app.agents.pipeline import executor

    if not executor.is_running(request_id):
        executor.start(request_id, S.PAYMENT_COMPLETED)


async def resolve_processing(sessions: async_sessionmaker[AsyncSession], request_id: uuid.UUID) -> None:
    """Restart recovery for a request stuck in PAYMENT_PROCESSING: ask PayPal what happened."""
    async with sessions() as db:
        payment_id = await db.scalar(
            select(Payment.id).join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
            .where(PurchaseOrder.request_id == request_id, Payment.status == P.PROCESSING).order_by(Payment.created_at.desc()).limit(1))
        if payment_id:
            await PaymentService(db).resolve(payment_id)


async def payments_for_request(db: AsyncSession, request_id: uuid.UUID) -> list[Payment]:
    return list((await db.scalars(select(Payment).join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
                                  .where(PurchaseOrder.request_id == request_id).order_by(Payment.created_at))).all())

