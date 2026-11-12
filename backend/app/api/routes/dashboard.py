from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models import Payment, ProcurementRequest, PurchaseOrder, User, WorkflowEvent
from app.models.enums import PaymentStatus, UserRole, WorkflowState as S
from app.workflow import TERMINAL

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

WAITING = {S.AWAITING_APPROVAL, S.APPROVED, S.PAYMENT_PENDING}


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    scope = ProcurementRequest.requester_id == user.id if user.role == UserRole.REQUESTER else True

    by_status = dict((await db.execute(select(ProcurementRequest.status, func.count()).where(scope).group_by(ProcurementRequest.status))).all())
    total = sum(by_status.values())
    active = sum(n for s, n in by_status.items() if s not in TERMINAL and s not in WAITING and s not in (S.CREATED, S.FAILED))

    paid = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
        .join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id).where(scope, Payment.status == PaymentStatus.COMPLETED)
    )
    events = (await db.execute(
        select(WorkflowEvent, ProcurementRequest.title).join(ProcurementRequest, ProcurementRequest.id == WorkflowEvent.request_id)
        .where(scope, WorkflowEvent.event_type != "agent.tool_called").order_by(WorkflowEvent.seq.desc()).limit(12)
    )).all()
    pos = (await db.scalars(
        select(PurchaseOrder).join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id).where(scope)
        .options(selectinload(PurchaseOrder.vendor)).order_by(PurchaseOrder.created_at.desc()).limit(6)
    )).all()

    return {
        "total_requests": total,
        "active_workflows": active,
        "pending_approvals": by_status.get(S.AWAITING_APPROVAL, 0),
        "completed_purchases": by_status.get(S.COMPLETED, 0),
        "total_transaction_value": str(Decimal(paid).quantize(Decimal("0.01"))),
        "requests_by_status": by_status,
        "recent_activity": [
            {"seq": e.seq, "request_id": str(e.request_id), "request_title": title, "event_type": e.event_type,
             "payload": e.payload, "created_at": e.created_at.isoformat()} for e, title in events
        ],
        "recent_purchase_orders": [
            {"id": str(p.id), "po_number": p.po_number, "vendor_name": p.vendor.name, "status": p.status, "total": str(p.total),
             "currency": p.currency, "request_id": str(p.request_id), "created_at": p.created_at.isoformat()} for p in pos
        ],
    }
