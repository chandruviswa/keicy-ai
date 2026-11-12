"""Reconciliation Agent: confirms a captured payment against PayPal's live record and closes the order.

Deterministic by design — no LLM. A payment is reconciled only if PayPal itself reports a COMPLETED
capture whose amount and currency match the purchase order and our stored capture.
"""

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.agents.core.base import AgentContext, BaseAgent
from app.agents.outcome import StepOutcome
from app.models.enums import PaymentStatus, PurchaseOrderStatus


class Receipt(BaseModel):
    receipt_number: str
    po_number: str
    purchase_order_id: uuid.UUID
    payment_id: uuid.UUID
    paypal_order_id: str
    capture_id: str
    amount: Decimal
    currency: str
    paid_at: str | None
    lines: list[dict]
    subtotal: Decimal
    tax_amount: Decimal


class ReconciliationResult(StepOutcome):
    payment_id: uuid.UUID | None = None
    receipt: Receipt | None = None
    checks: list[str] = Field(default_factory=list)
    discrepancies: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the Reconciliation Agent of Keicy AI. You confirm that money PayPal reports as
captured matches the approved purchase order exactly, then close the order and issue a receipt.
You rely only on PayPal's API and the database; a payment you cannot verify is never marked complete."""


class ReconciliationAgent(BaseAgent):
    name = "reconciliation"
    description = "Verifies the PayPal capture, marks the PO paid and issues the receipt."
    system_prompt = SYSTEM_PROMPT
    timeout_s = 90.0
    max_tool_calls = 10

    async def run(self, ctx: AgentContext) -> ReconciliationResult:
        status = await ctx.tool("retrieve_payment_status", {})
        completed = [p for p in status.payments if p.status == PaymentStatus.COMPLETED]
        if len(completed) != 1:
            return ReconciliationResult(outcome="reject", discrepancies=[f"expected one completed payment, found {len(completed)}"],
                                        reason=f"Reconciliation failed: {len(completed)} completed payments for this request")
        pay = completed[0]
        if len(pay.paypal_order_ids) != 1:
            return ReconciliationResult(outcome="reject", payment_id=pay.payment_id, reason="Reconciliation failed: payment has no single PayPal order")
        order_id = pay.paypal_order_ids[0]

        live = await ctx.tool("get_paypal_order", {"paypal_order_id": order_id})
        verify = await ctx.tool("verify_paypal_capture", {"paypal_order_id": order_id})
        checks = [f"PayPal order {order_id} is {live.status}", f"{len(live.captures)} capture(s) reported by PayPal"]
        if not verify.verified:
            return ReconciliationResult(outcome="reject", payment_id=pay.payment_id, checks=checks, discrepancies=verify.issues,
                                        reason="Reconciliation found discrepancies: " + "; ".join(verify.issues))
        checks.append(f"captured {verify.captured_amount} {verify.currency} = purchase order total {verify.expected_amount}")

        po = await ctx.tool("get_purchase_order", {"purchase_order_id": str(pay.purchase_order_id)})
        if po.status != PurchaseOrderStatus.PAID:
            po = await ctx.tool("update_purchase_order",
                                {"purchase_order_id": str(po.purchase_order_id), "status": PurchaseOrderStatus.PAID,
                                 "notes": f"Paid via PayPal capture {verify.capture_id}"},
                                idempotency_key=ctx.idempotency_key(f"po_paid:{po.purchase_order_id}"))
            po = await ctx.tool("get_purchase_order", {"purchase_order_id": str(po.purchase_order_id)})
        checks.append(f"purchase order {po.po_number} marked {po.status}")

        capture = next(c for c in live.captures if c.capture_id == verify.capture_id)
        receipt = Receipt(
            receipt_number=f"RCPT-{po.po_number.removeprefix('PO-')}", po_number=po.po_number, purchase_order_id=po.purchase_order_id,
            payment_id=pay.payment_id, paypal_order_id=order_id, capture_id=capture.capture_id, amount=capture.amount,
            currency=capture.currency, paid_at=capture.create_time, subtotal=po.subtotal, tax_amount=po.tax_amount,
            lines=[{"description": ln.description, "quantity": ln.quantity, "unit_price": str(ln.unit_price), "line_total": str(ln.line_total)}
                   for ln in po.lines or []],
        )
        return ReconciliationResult(payment_id=pay.payment_id, receipt=receipt, checks=checks)
