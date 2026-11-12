from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models import (
    AgentRun, Approval, Payment, ProcurementRequest, Product, PurchaseOrder, PurchaseOrderItem, User, Vendor, VendorOffer,
    WorkflowEvent, WorkflowStateHistory,
)
from app.models.enums import PaymentStatus, UserRole, WorkflowState as S
from app.workflow import TERMINAL

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

WAITING = {S.AWAITING_APPROVAL, S.APPROVED, S.PAYMENT_PENDING}
CENT = Decimal("0.01")
TREND_DAYS = 30

# Where each request currently sits, in the order work flows through the desk.
STAGES = [
    ("intake", "Submitted", {S.CREATED}),
    ("agents", "Agents working", {S.ANALYZING, S.RESEARCHING, S.EVALUATING, S.ORDER_DRAFTED, S.FINANCE_VALIDATING}),
    ("approval", "Awaiting approval", {S.AWAITING_APPROVAL}),
    ("payment", "Payment", {S.APPROVED, S.PAYMENT_PENDING, S.PAYMENT_PROCESSING, S.PAYMENT_FAILED, S.PAYMENT_COMPLETED, S.RECONCILING}),
    ("completed", "Completed", {S.COMPLETED}),
    ("attention", "Needs attention", {S.FAILED}),
    ("closed", "Rejected or cancelled", {S.REJECTED, S.CANCELLED}),
]


def money(v) -> str:
    return str(Decimal(v or 0).quantize(CENT))


def hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    """Everything the Overview shows. Requesters see their own requests; approvers and admins see the whole desk.
    Catalogue figures are always organisation-wide."""
    scope = ProcurementRequest.requester_id == user.id if user.role == UserRole.REQUESTER else True
    po_scope = select(PurchaseOrder.id).join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id).where(scope)
    today = datetime.now(timezone.utc).date()

    # ---------- requests & pipeline ----------
    by_status = dict((await db.execute(select(ProcurementRequest.status, func.count()).where(scope).group_by(ProcurementRequest.status))).all())
    total = sum(by_status.values())
    active = sum(n for s, n in by_status.items() if s not in TERMINAL and s not in WAITING and s not in (S.CREATED, S.FAILED))
    pipeline = [{"key": k, "label": label, "count": sum(by_status.get(s, 0) for s in states)} for k, label, states in STAGES]

    # ---------- money ----------
    po_totals = dict((await db.execute(
        select(PurchaseOrder.status, func.coalesce(func.sum(PurchaseOrder.total), 0)).where(PurchaseOrder.id.in_(po_scope))
        .group_by(PurchaseOrder.status)
    )).all())
    paid_rows = (await db.execute(
        select(Payment.amount, Payment.updated_at, Vendor.name).join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(Payment.purchase_order_id.in_(po_scope), Payment.status == PaymentStatus.COMPLETED)
    )).all()
    paid = sum((r.amount for r in paid_rows), Decimal(0))

    start = today - timedelta(days=TREND_DAYS - 1)
    per_day: dict[date, Decimal] = defaultdict(Decimal)
    by_vendor: dict[str, list] = defaultdict(lambda: [Decimal(0), 0])
    for r in paid_rows:
        d = r.updated_at.astimezone(timezone.utc).date()
        if d >= start:
            per_day[d] += r.amount
        by_vendor[r.name][0] += r.amount
        by_vendor[r.name][1] += 1
    prev_start = start - timedelta(days=TREND_DAYS)
    last_period = sum((r.amount for r in paid_rows if r.updated_at.astimezone(timezone.utc).date() >= start), Decimal(0))
    prev_period = sum((r.amount for r in paid_rows if prev_start <= r.updated_at.astimezone(timezone.utc).date() < start), Decimal(0))

    by_category = (await db.execute(
        select(Product.category, func.sum(PurchaseOrderItem.line_total)).join(Product, Product.id == PurchaseOrderItem.product_id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.id.in_(po_scope), PurchaseOrder.status == "PAID").group_by(Product.category)
        .order_by(func.sum(PurchaseOrderItem.line_total).desc())
    )).all()

    # Savings: what each ordered line would have cost at the highest quote for the same product.
    highest = select(VendorOffer.product_id, func.max(VendorOffer.unit_price).label("top")).group_by(VendorOffer.product_id).subquery()
    saved = await db.scalar(
        select(func.coalesce(func.sum(func.greatest((highest.c.top - PurchaseOrderItem.unit_price) * PurchaseOrderItem.quantity, 0)), 0))
        .join(highest, highest.c.product_id == PurchaseOrderItem.product_id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrder.id.in_(po_scope), PurchaseOrder.status.in_(["APPROVED", "PAID"]))
    )

    # ---------- speed ----------
    hist = (await db.execute(
        select(WorkflowStateHistory.request_id, WorkflowStateHistory.to_state, WorkflowStateHistory.created_at)
        .join(ProcurementRequest, ProcurementRequest.id == WorkflowStateHistory.request_id).where(scope)
        .order_by(WorkflowStateHistory.created_at)
    )).all()
    firsts: dict = defaultdict(dict)
    for h in hist:
        firsts[h.request_id].setdefault(h.to_state, h.created_at)
    to_po, to_decision, to_done = [], [], []
    for states in firsts.values():
        created = states.get(S.CREATED)
        awaiting = states.get(S.AWAITING_APPROVAL)
        decided = states.get(S.APPROVED) or states.get(S.REJECTED)
        if created and awaiting:
            to_po.append(hours(created, awaiting) * 60)
        if awaiting and decided and decided >= awaiting:
            to_decision.append(hours(awaiting, decided))
        if created and S.COMPLETED in states:
            to_done.append(hours(created, states[S.COMPLETED]))

    decisions = dict((await db.execute(
        select(Approval.decision, func.count()).where(Approval.purchase_order_id.in_(po_scope)).group_by(Approval.decision)
    )).all())
    runs = (await db.execute(
        select(AgentRun.status, AgentRun.started_at, AgentRun.finished_at)
        .join(ProcurementRequest, ProcurementRequest.id == AgentRun.request_id).where(scope)
    )).all()
    run_secs = [(r.finished_at - r.started_at).total_seconds() for r in runs if r.started_at and r.finished_at]

    # ---------- catalogue (organisation-wide) ----------
    live = (VendorOffer.is_active.is_(True), VendorOffer.valid_until >= today, VendorOffer.available_quantity > 0)
    catalogue = {
        "vendors": await db.scalar(select(func.count()).select_from(Vendor).where(Vendor.is_active.is_(True))),
        "products": await db.scalar(select(func.count()).select_from(Product)),
        "live_offers": await db.scalar(select(func.count()).select_from(VendorOffer).join(Vendor).where(*live, Vendor.is_active.is_(True))),
        "expiring_soon": await db.scalar(select(func.count()).select_from(VendorOffer).where(
            VendorOffer.is_active.is_(True), VendorOffer.valid_until >= today, VendorOffer.valid_until <= today + timedelta(days=14))),
        "out_of_stock": await db.scalar(select(func.count()).select_from(VendorOffer).where(
            VendorOffer.is_active.is_(True), VendorOffer.available_quantity == 0)),
        "expired": await db.scalar(select(func.count()).select_from(VendorOffer).where(
            VendorOffer.is_active.is_(True), VendorOffer.valid_until < today)),
    }

    # ---------- recent ----------
    events = (await db.execute(
        select(WorkflowEvent, ProcurementRequest.title).join(ProcurementRequest, ProcurementRequest.id == WorkflowEvent.request_id)
        .where(scope, WorkflowEvent.event_type != "agent.tool_called").order_by(WorkflowEvent.seq.desc()).limit(12)
    )).all()
    pos = (await db.scalars(
        select(PurchaseOrder).join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id).where(scope)
        .options(selectinload(PurchaseOrder.vendor)).order_by(PurchaseOrder.created_at.desc()).limit(6)
    )).all()

    approved, rejected = decisions.get("APPROVED", 0), decisions.get("REJECTED", 0)
    succeeded = sum(1 for r in runs if r.status == "SUCCEEDED")
    finished = sum(1 for r in runs if r.status in ("SUCCEEDED", "FAILED", "TIMED_OUT"))
    return {
        "scope": "mine" if user.role == UserRole.REQUESTER else "organisation",
        "total_requests": total,
        "active_workflows": active,
        "pending_approvals": by_status.get(S.AWAITING_APPROVAL, 0),
        "completed_purchases": by_status.get(S.COMPLETED, 0),
        "total_transaction_value": money(paid),
        "requests_by_status": by_status,
        "pipeline": pipeline,
        "money": {
            "paid": money(paid),
            "paid_last_30d": money(last_period),
            "paid_prev_30d": money(prev_period),
            "awaiting_approval": money(po_totals.get("PENDING_APPROVAL")),
            "ready_to_pay": money(po_totals.get("APPROVED")),
            "saved_vs_highest_quote": money(saved),
            "payments": len(paid_rows),
            "average_order": money(paid / len(paid_rows)) if paid_rows else "0.00",
        },
        "spend_by_day": [{"date": (start + timedelta(days=i)).isoformat(), "amount": money(per_day.get(start + timedelta(days=i)))}
                         for i in range(TREND_DAYS)],
        "spend_by_vendor": [{"vendor": n, "amount": money(a), "orders": c}
                            for n, (a, c) in sorted(by_vendor.items(), key=lambda kv: kv[1][0], reverse=True)[:6]],
        "spend_by_category": [{"category": c, "amount": money(a)} for c, a in by_category],
        "speed": {
            "median_minutes_to_po": round(median(to_po), 1) if to_po else None,
            "median_hours_to_decision": round(median(to_decision), 1) if to_decision else None,
            "median_hours_to_complete": round(median(to_done), 1) if to_done else None,
        },
        "approvals": {"approved": approved, "rejected": rejected,
                      "approval_rate": round(approved / (approved + rejected) * 100) if approved + rejected else None},
        "agents": {"runs": len(runs), "succeeded": succeeded,
                   "success_rate": round(succeeded / finished * 100) if finished else None,
                   "median_seconds": round(median(run_secs), 1) if run_secs else None},
        "catalogue": catalogue,
        "recent_activity": [
            {"seq": e.seq, "request_id": str(e.request_id), "request_title": title, "event_type": e.event_type,
             "payload": e.payload, "created_at": e.created_at.isoformat()} for e, title in events
        ],
        "recent_purchase_orders": [
            {"id": str(p.id), "po_number": p.po_number, "vendor_name": p.vendor.name, "status": p.status, "total": str(p.total),
             "currency": p.currency, "request_id": str(p.request_id), "created_at": p.created_at.isoformat()} for p in pos
        ],
    }
