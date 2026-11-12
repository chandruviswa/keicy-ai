"""Payment tools.

Read tools (get_paypal_order, verify_paypal_capture, retrieve_payment_status) let the Reconciliation
Agent check PayPal's live record against ours. The two money-moving tools exist in the registry for
completeness but refuse to run: no agent is granted them, and even if one were, orders and captures
happen only through the payment API after a human approval and a buyer approval at PayPal.
"""

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.core.tools import ToolBusinessError, ToolContext
from app.models import Payment, PayPalCapture, PayPalOrder, PurchaseOrder
from app.models.enums import PaymentStatus
from app.payments.paypal import PayPalError, PayPalTimeout, captures_of, get_client, order_amount
from app.tools.common import money, registry, source


class PayPalOrderRef(BaseModel):
    paypal_order_id: str = Field(min_length=1, max_length=64)


class CaptureView(BaseModel):
    capture_id: str
    status: str
    amount: Decimal
    currency: str
    create_time: str | None = None


class PayPalOrderView(BaseModel):
    paypal_order_id: str
    status: str
    amount: Decimal | None
    currency: str | None
    captures: list[CaptureView] = Field(default_factory=list)
    source: dict[str, str]


class PreparePayPalOrderArgs(BaseModel):
    payment_id: uuid.UUID


class VerifyCaptureResult(BaseModel):
    verified: bool
    paypal_order_id: str
    capture_id: str | None
    captured_amount: Decimal | None
    expected_amount: Decimal
    currency: str
    issues: list[str]


async def _own_order(ctx: ToolContext, paypal_order_id: str) -> tuple[PayPalOrder, Payment, PurchaseOrder]:
    """The PayPal order must belong to a payment for this request's purchase order."""
    row = (await ctx.db.execute(
        select(PayPalOrder, Payment, PurchaseOrder).join(Payment, Payment.id == PayPalOrder.payment_id)
        .join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
        .where(PayPalOrder.paypal_order_id == paypal_order_id, PurchaseOrder.request_id == ctx.request_id)
    )).first()
    if row is None:
        raise ToolBusinessError(f"PayPal order {paypal_order_id} not found for this request")
    return row


async def _live_order(paypal_order_id: str) -> dict:
    try:
        return await get_client().get_order(paypal_order_id)
    except PayPalTimeout as e:
        raise ToolBusinessError(f"PayPal did not respond: {e}") from e
    except PayPalError as e:
        raise ToolBusinessError(f"PayPal returned {e.status} for order {paypal_order_id}") from e


def _caps(order: dict) -> list[CaptureView]:
    return [CaptureView(capture_id=c["id"], status=c["status"], amount=Decimal(c["amount"]["value"]),
                        currency=c["amount"]["currency_code"], create_time=c.get("create_time")) for c in captures_of(order)]


@registry.tool("prepare_paypal_order", args=PreparePayPalOrderArgs, result=PayPalOrderView, is_write=True, group="payments",
               description="Disabled for agents: PayPal orders are created only via POST /payments/create-order by the requester.")
async def prepare_paypal_order(ctx: ToolContext, a: PreparePayPalOrderArgs) -> PayPalOrderView:
    raise ToolBusinessError("Agents cannot create PayPal orders; the requester starts checkout via the payment API")


@registry.tool("capture_approved_payment", args=PayPalOrderRef, result=CaptureView, is_write=True, group="payments",
               description="Disabled for agents: captures happen only via POST /payments/capture after the buyer approves at PayPal.")
async def capture_approved_payment(ctx: ToolContext, a: PayPalOrderRef) -> CaptureView:
    raise ToolBusinessError("Agents cannot capture payments; capture happens only after the buyer approves at PayPal")


@registry.tool("get_paypal_order", args=PayPalOrderRef, result=PayPalOrderView, group="payments",
               description="Fetch this request's PayPal order live from PayPal, including its captures.")
async def get_paypal_order(ctx: ToolContext, a: PayPalOrderRef) -> PayPalOrderView:
    ppo, _, _ = await _own_order(ctx, a.paypal_order_id)
    order = await _live_order(a.paypal_order_id)
    amount, currency = order_amount(order)
    return PayPalOrderView(paypal_order_id=order["id"], status=order.get("status", "UNKNOWN"), amount=amount, currency=currency,
                           captures=_caps(order), source=source("paypal_orders", ppo.id))


@registry.tool("verify_paypal_capture", args=PayPalOrderRef, result=VerifyCaptureResult, group="payments",
               description="Check PayPal's live capture against our purchase order and stored capture: status, amount, currency, ids.")
async def verify_paypal_capture(ctx: ToolContext, a: PayPalOrderRef) -> VerifyCaptureResult:
    ppo, payment, po = await _own_order(ctx, a.paypal_order_id)
    order = await _live_order(a.paypal_order_id)
    live = [c for c in _caps(order) if c.status == "COMPLETED"]
    stored = {c.capture_id: c for c in (await ctx.db.scalars(select(PayPalCapture).where(PayPalCapture.paypal_order_ref_id == ppo.id))).all()}
    issues = []
    captured = money(sum((c.amount for c in live), Decimal("0"))) if live else None
    if not live:
        issues.append(f"PayPal shows no completed capture (order status {order.get('status')})")
    else:
        if captured != po.total:
            issues.append(f"PayPal captured {captured}, purchase order total is {po.total}")
        if {c.currency for c in live} != {po.currency}:
            issues.append(f"PayPal capture currency {sorted({c.currency for c in live})} ≠ {po.currency}")
        for c in live:
            mine = stored.get(c.capture_id)
            if mine is None:
                issues.append(f"capture {c.capture_id} is not in our records")
            elif mine.amount != c.amount or mine.currency != c.currency:
                issues.append(f"capture {c.capture_id}: our record {mine.amount} {mine.currency} ≠ PayPal {c.amount} {c.currency}")
    if payment.status != PaymentStatus.COMPLETED:
        issues.append(f"our payment record is {payment.status}")
    if payment.amount != po.total:
        issues.append(f"payment amount {payment.amount} ≠ purchase order total {po.total}")
    return VerifyCaptureResult(verified=not issues, paypal_order_id=a.paypal_order_id, capture_id=live[0].capture_id if live else None,
                               captured_amount=captured, expected_amount=po.total, currency=po.currency, issues=issues)


# ---------- retrieve_payment_status (read, from our DB) ----------


class Empty(BaseModel):
    pass


class PaymentStatusRow(BaseModel):
    payment_id: uuid.UUID
    purchase_order_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    attempt: int
    paypal_order_ids: list[str]
    capture_ids: list[str]
    source: dict[str, str]


class PaymentStatusResult(BaseModel):
    payments: list[PaymentStatusRow]


@registry.tool("retrieve_payment_status", args=Empty, result=PaymentStatusResult, group="payments",
               description="Payment records (with PayPal order and capture IDs) stored for this request.")
async def retrieve_payment_status(ctx: ToolContext, a: Empty) -> PaymentStatusResult:
    payments = (await ctx.db.scalars(
        select(Payment).join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
        .where(PurchaseOrder.request_id == ctx.request_id).order_by(Payment.created_at)
    )).all()
    rows = []
    for p in payments:
        orders = (await ctx.db.scalars(select(PayPalOrder).where(PayPalOrder.payment_id == p.id))).all()
        caps = (await ctx.db.scalars(select(PayPalCapture.capture_id).where(PayPalCapture.paypal_order_ref_id.in_([o.id for o in orders])))).all() if orders else []
        rows.append(PaymentStatusRow(payment_id=p.id, purchase_order_id=p.purchase_order_id, amount=p.amount, currency=p.currency,
                                     status=p.status, attempt=p.attempt, paypal_order_ids=[o.paypal_order_id for o in orders],
                                     capture_ids=list(caps), source=source("payments", p.id)))
    return PaymentStatusResult(payments=rows)
