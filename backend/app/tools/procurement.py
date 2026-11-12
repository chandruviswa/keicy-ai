"""Procurement tools. Totals are always recomputed from database prices — never taken from the caller."""

import secrets
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.core.tools import ToolBusinessError, ToolContext
from app.agents.schemas import ProcurementRequirement
from app.models import ProcurementRequest, PurchaseOrder, PurchaseOrderItem, VendorOffer
from app.models.enums import PurchaseOrderStatus
from app.tools.common import SUPPORTED_CURRENCIES, money, offer_issues, registry, source

# ---------- validate_requirements ----------


class ValidateRequirementsResult(BaseModel):
    valid: bool
    issues: list[str]
    missing_fields: list[str]


@registry.tool("validate_requirements", args=ProcurementRequirement, result=ValidateRequirementsResult, group="procurement",
               description="Check structured requirements for completeness and business validity (quantity, budget, currency, deadline).")
async def validate_requirements(ctx: ToolContext, r: ProcurementRequirement) -> ValidateRequirementsResult:
    # Schema checks (quantity ≥ 1, currency format, ...) already ran; these are business rules.
    issues, missing = [], []
    if r.budget is None:
        missing.append("budget")
    if r.currency not in SUPPORTED_CURRENCIES:
        issues.append(f"currency {r.currency} not supported (use one of {sorted(SUPPORTED_CURRENCIES)})")
    if r.deadline and r.deadline < date.today():
        issues.append(f"deadline {r.deadline} is in the past")
    for i, item in enumerate(r.items):
        if len(item.product_name.strip()) < 2:
            issues.append(f"item {i + 1}: product name too vague")
    return ValidateRequirementsResult(valid=not issues and not missing, issues=issues, missing_fields=missing)


# ---------- calculate_purchase_total ----------


class LineRequest(BaseModel):
    offer_id: uuid.UUID
    quantity: int = Field(ge=1, le=1_000_000)


class CalculateTotalArgs(BaseModel):
    lines: list[LineRequest] = Field(min_length=1, max_length=50)
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=Decimal("0.5"), description="e.g. 0.0825 for 8.25%")


class PricedLine(BaseModel):
    offer_id: uuid.UUID
    product_id: uuid.UUID
    vendor_id: uuid.UUID
    description: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    issues: list[str]
    source: dict[str, str]


class PurchaseTotal(BaseModel):
    lines: list[PricedLine]
    currency: str
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    vendor_ids: list[uuid.UUID]
    all_lines_available: bool


async def _price(ctx: ToolContext, a: CalculateTotalArgs) -> PurchaseTotal:
    ids = [ln.offer_id for ln in a.lines]
    offers = {o.id: o for o in (await ctx.db.scalars(
        select(VendorOffer).where(VendorOffer.id.in_(ids)).options(selectinload(VendorOffer.vendor), selectinload(VendorOffer.product))
    )).all()}
    missing = [str(i) for i in ids if i not in offers]
    if missing:
        raise ToolBusinessError(f"Unknown offer id(s): {', '.join(missing)}")
    currencies = {offers[i].currency for i in ids}
    if len(currencies) > 1:
        raise ToolBusinessError(f"Lines use different currencies: {sorted(currencies)}")

    lines = []
    for ln in a.lines:
        o = offers[ln.offer_id]
        lines.append(PricedLine(
            offer_id=o.id, product_id=o.product_id, vendor_id=o.vendor_id, description=f"{o.product.name} ({o.product.sku})",
            quantity=ln.quantity, unit_price=o.unit_price, line_total=money(o.unit_price * ln.quantity),
            issues=offer_issues(o, ln.quantity), source=source("vendor_offers", o.id),
        ))
    subtotal = money(sum((ln.line_total for ln in lines), Decimal("0")))
    tax = money(subtotal * a.tax_rate)
    return PurchaseTotal(
        lines=lines, currency=currencies.pop(), subtotal=subtotal, tax_rate=a.tax_rate, tax_amount=tax, total=subtotal + tax,
        vendor_ids=sorted({ln.vendor_id for ln in lines}, key=str), all_lines_available=all(not ln.issues for ln in lines),
    )


@registry.tool("calculate_purchase_total", args=CalculateTotalArgs, result=PurchaseTotal, group="procurement",
               description="Compute line totals, subtotal, tax and total from current offer prices in the database.")
async def calculate_purchase_total(ctx: ToolContext, a: CalculateTotalArgs) -> PurchaseTotal:
    return await _price(ctx, a)


# ---------- create_purchase_order (write) ----------


class CreatePOArgs(CalculateTotalArgs):
    notes: str | None = Field(None, max_length=2000)


class PurchaseOrderView(BaseModel):
    purchase_order_id: uuid.UUID
    po_number: str
    request_id: uuid.UUID
    vendor_id: uuid.UUID
    status: str
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    lines: list[PricedLine] | None = None
    source: dict[str, str]


def new_po_number(today: date | None = None) -> str:
    return f"PO-{(today or datetime.now(timezone.utc).date()):%Y%m%d}-{secrets.token_hex(3).upper()}"


@registry.tool("create_purchase_order", args=CreatePOArgs, result=PurchaseOrderView, is_write=True, group="procurement",
               description="Create a DRAFT purchase order for this request from offer lines (one vendor per PO). Requires idempotency_key.")
async def create_purchase_order(ctx: ToolContext, a: CreatePOArgs) -> PurchaseOrderView:
    priced = await _price(ctx, a)
    if len(priced.vendor_ids) != 1:
        raise ToolBusinessError("A purchase order must contain offers from exactly one vendor")
    unavailable = [f"{ln.description}: {', '.join(ln.issues)}" for ln in priced.lines if ln.issues]
    if unavailable:
        raise ToolBusinessError("Cannot order unavailable lines — " + "; ".join(unavailable))
    request = await ctx.db.get(ProcurementRequest, ctx.request_id)
    if request is None:
        raise ToolBusinessError("Procurement request not found")
    if priced.currency != request.currency:
        raise ToolBusinessError(f"Offer currency {priced.currency} differs from request currency {request.currency}")

    po = PurchaseOrder(po_number=new_po_number(), request_id=ctx.request_id, vendor_id=priced.vendor_ids[0],
                       status=PurchaseOrderStatus.DRAFT, currency=priced.currency, subtotal=priced.subtotal,
                       tax_amount=priced.tax_amount, total=priced.total, notes=a.notes)
    ctx.db.add(po)
    await ctx.db.flush()
    for ln in priced.lines:
        ctx.db.add(PurchaseOrderItem(purchase_order_id=po.id, offer_id=ln.offer_id, product_id=ln.product_id, description=ln.description,
                                     quantity=ln.quantity, unit_price=ln.unit_price, line_total=ln.line_total))
    await ctx.db.flush()
    return _po_view(po, priced.lines)


def _po_view(po: PurchaseOrder, lines: list[PricedLine] | None = None) -> PurchaseOrderView:
    return PurchaseOrderView(
        purchase_order_id=po.id, po_number=po.po_number, request_id=po.request_id, vendor_id=po.vendor_id, status=po.status,
        currency=po.currency, subtotal=po.subtotal, tax_amount=po.tax_amount, total=po.total, lines=lines,
        source=source("purchase_orders", po.id),
    )


# ---------- get_purchase_order ----------


class GetPOArgs(BaseModel):
    purchase_order_id: uuid.UUID | None = Field(None, description="Omit to get the latest PO for the current request")


async def load_po(ctx: ToolContext, po_id: uuid.UUID | None) -> PurchaseOrder:
    stmt = select(PurchaseOrder).options(selectinload(PurchaseOrder.items)).where(PurchaseOrder.request_id == ctx.request_id)
    stmt = stmt.where(PurchaseOrder.id == po_id) if po_id else stmt.order_by(PurchaseOrder.created_at.desc()).limit(1)
    po = await ctx.db.scalar(stmt)
    if po is None:  # also hides POs that belong to other requests
        raise ToolBusinessError("Purchase order not found for this request")
    return po


@registry.tool("get_purchase_order", args=GetPOArgs, result=PurchaseOrderView, group="procurement",
               description="Read a purchase order (and its lines) belonging to the current request.")
async def get_purchase_order(ctx: ToolContext, a: GetPOArgs) -> PurchaseOrderView:
    po = await load_po(ctx, a.purchase_order_id)
    lines = [PricedLine(offer_id=i.offer_id, product_id=i.product_id, vendor_id=po.vendor_id, description=i.description,
                        quantity=i.quantity, unit_price=i.unit_price, line_total=i.line_total, issues=[],
                        source=source("purchase_order_items", i.id)) for i in po.items]
    return _po_view(po, lines)


# ---------- update_purchase_order (write) — Part 3 ----------


async def _require_verified_payment(ctx: ToolContext, po: PurchaseOrder) -> None:
    """PAID only by the Reconciliation Agent, only for an APPROVED PO with a COMPLETED payment of the exact total."""
    from app.models import Payment
    from app.models.enums import PaymentStatus

    if ctx.agent_name != "reconciliation":
        raise ToolBusinessError("Only the Reconciliation Agent can mark a purchase order PAID")
    if po.status != PurchaseOrderStatus.APPROVED:
        raise ToolBusinessError(f"Purchase order is {po.status}; only APPROVED orders can become PAID")
    paid = await ctx.db.scalar(select(Payment.id).where(Payment.purchase_order_id == po.id, Payment.status == PaymentStatus.COMPLETED,
                                                        Payment.amount == po.total))
    if paid is None:
        raise ToolBusinessError("No completed payment for the full purchase order total")


class UpdatePOArgs(BaseModel):
    purchase_order_id: uuid.UUID
    status: PurchaseOrderStatus | None = None
    notes: str | None = Field(None, max_length=2000)


# What agents may do to a PO's status. APPROVED / REJECTED come only from the human approval API,
# PAID only from verified PayPal captures (Part 4).
AGENT_PO_TRANSITIONS: dict[str, frozenset[str]] = {
    PurchaseOrderStatus.DRAFT: frozenset({PurchaseOrderStatus.PENDING_APPROVAL, PurchaseOrderStatus.CANCELLED}),
    PurchaseOrderStatus.PENDING_APPROVAL: frozenset({PurchaseOrderStatus.CANCELLED}),
}


@registry.tool("update_purchase_order", args=UpdatePOArgs, result=PurchaseOrderView, is_write=True, group="procurement",
               description="Move a PO DRAFT → PENDING_APPROVAL or cancel it, and/or append notes. Agents can never approve, reject or mark paid.")
async def update_purchase_order(ctx: ToolContext, a: UpdatePOArgs) -> PurchaseOrderView:
    po = await load_po(ctx, a.purchase_order_id)
    if a.status is not None and a.status != po.status:
        if a.status in (PurchaseOrderStatus.APPROVED, PurchaseOrderStatus.REJECTED):
            raise ToolBusinessError("Only a human approver can approve or reject a purchase order")
        if a.status == PurchaseOrderStatus.PAID:
            await _require_verified_payment(ctx, po)
        elif a.status not in AGENT_PO_TRANSITIONS.get(po.status, frozenset()):
            raise ToolBusinessError(f"Purchase order cannot move from {po.status} to {a.status}")
        po.status = a.status
    if a.notes:
        po.notes = f"{po.notes}\n{a.notes}" if po.notes else a.notes
    await ctx.db.flush()
    return _po_view(po)
