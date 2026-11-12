"""Finance tools. Budgets and approvals are read from the database, never from model output."""

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.core.tools import ToolBusinessError, ToolContext
from app.core.config import get_settings
from app.models import Approval, Payment, ProcurementRequest, PurchaseOrder
from app.models.enums import PaymentStatus, PurchaseOrderStatus
from app.tools.common import SUPPORTED_CURRENCIES, money, registry
from app.workflow import has_approval


async def _request(ctx: ToolContext) -> ProcurementRequest:
    pr = await ctx.db.get(ProcurementRequest, ctx.request_id)
    if pr is None:
        raise ToolBusinessError("Procurement request not found")
    return pr


# ---------- validate_budget ----------


class ValidateBudgetArgs(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ValidateBudgetResult(BaseModel):
    within_budget: bool
    budget: Decimal | None
    amount: Decimal
    remaining: Decimal | None
    needs_approval: bool  # above the auto-approval threshold
    approval_threshold: Decimal
    issues: list[str]


@registry.tool("validate_budget", args=ValidateBudgetArgs, result=ValidateBudgetResult, group="finance",
               description="Compare an amount with the request's budget and the approval threshold.")
async def validate_budget(ctx: ToolContext, a: ValidateBudgetArgs) -> ValidateBudgetResult:
    pr = await _request(ctx)
    threshold = money(get_settings().approval_threshold)
    issues = []
    if a.currency != pr.currency:
        issues.append(f"amount currency {a.currency} differs from request currency {pr.currency}")
    if pr.budget is None:
        issues.append("request has no budget")
    elif a.amount > pr.budget:
        issues.append(f"amount {a.amount} exceeds budget {pr.budget} by {money(a.amount - pr.budget)}")
    return ValidateBudgetResult(
        within_budget=not issues, budget=pr.budget, amount=money(a.amount),
        remaining=money(pr.budget - a.amount) if pr.budget is not None else None,
        needs_approval=True,  # every purchase needs explicit human approval; threshold flags extra scrutiny
        approval_threshold=threshold, issues=issues,
    )


# ---------- validate_currency ----------


class ValidateCurrencyArgs(BaseModel):
    currency: str = Field(min_length=3, max_length=3)


class ValidateCurrencyResult(BaseModel):
    currency: str
    supported: bool
    matches_request: bool
    supported_currencies: list[str]


@registry.tool("validate_currency", args=ValidateCurrencyArgs, result=ValidateCurrencyResult, group="finance",
               description="Check a currency is supported for payment and matches the request.")
async def validate_currency(ctx: ToolContext, a: ValidateCurrencyArgs) -> ValidateCurrencyResult:
    pr = await _request(ctx)
    cur = a.currency.upper()
    return ValidateCurrencyResult(currency=cur, supported=cur in SUPPORTED_CURRENCIES, matches_request=cur == pr.currency,
                                  supported_currencies=sorted(SUPPORTED_CURRENCIES))


# ---------- check_approval_status ----------


class Empty(BaseModel):
    pass


class ApprovalStatusResult(BaseModel):
    approved: bool
    latest_decision: str | None
    purchase_order_id: uuid.UUID | None
    approver_id: uuid.UUID | None
    reason: str | None
    decided_at: str | None


@registry.tool("check_approval_status", args=Empty, result=ApprovalStatusResult, group="finance",
               description="Latest human approval decision for this request's purchase orders.")
async def check_approval_status(ctx: ToolContext, a: Empty) -> ApprovalStatusResult:
    latest = await ctx.db.scalar(
        select(Approval).join(PurchaseOrder, PurchaseOrder.id == Approval.purchase_order_id)
        .where(PurchaseOrder.request_id == ctx.request_id).order_by(Approval.decided_at.desc(), Approval.created_at.desc()).limit(1)
    )
    return ApprovalStatusResult(
        approved=await has_approval(ctx.db, ctx.request_id),
        latest_decision=latest.decision if latest else None,
        purchase_order_id=latest.purchase_order_id if latest else None,
        approver_id=latest.approver_id if latest else None,
        reason=latest.reason if latest else None,
        decided_at=latest.decided_at.isoformat() if latest else None,
    )


# ---------- prepare_payment_request (write) — Part 4 ----------


class PreparePaymentArgs(BaseModel):
    purchase_order_id: uuid.UUID


class PaymentRequestView(BaseModel):
    payment_id: uuid.UUID
    purchase_order_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str


@registry.tool("prepare_payment_request", args=PreparePaymentArgs, result=PaymentRequestView, is_write=True, group="finance",
               description="Create (or return) the internal payment record for a human-approved PO, priced from the PO. No money moves.")
async def prepare_payment_request(ctx: ToolContext, a: PreparePaymentArgs) -> PaymentRequestView:
    from app.payments.service import PaymentError, server_total
    from app.tools.procurement import load_po

    po = await load_po(ctx, a.purchase_order_id)
    if po.status != PurchaseOrderStatus.APPROVED or not await has_approval(ctx.db, ctx.request_id):
        raise ToolBusinessError(f"Purchase order {po.po_number} is {po.status}; a human must approve it first")
    try:
        amount = server_total(po)
    except PaymentError as e:
        raise ToolBusinessError(e.message) from e
    existing = await ctx.db.scalar(select(Payment).where(Payment.purchase_order_id == po.id,
                                                         Payment.status.in_([PaymentStatus.CREATED, PaymentStatus.PENDING])))
    if existing is None:
        attempt = (await ctx.db.scalar(select(func.coalesce(func.max(Payment.attempt), 0)).where(Payment.purchase_order_id == po.id))) + 1
        existing = Payment(purchase_order_id=po.id, amount=amount, currency=po.currency, status=PaymentStatus.CREATED,
                           attempt=attempt, idempotency_key=f"po-{po.id}-attempt-{attempt}")
        ctx.db.add(existing)
        await ctx.db.flush()
    return PaymentRequestView(payment_id=existing.id, purchase_order_id=po.id, amount=existing.amount, currency=existing.currency, status=existing.status)
