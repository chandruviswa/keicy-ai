"""Purchase orders and the human approval decision. Only approvers/admins can approve or reject."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import can_view, get_current_user, require_role
from app.api.schemas import ApproveIn, PurchaseOrderOut, RejectIn
from app.db.session import get_session
from app.models import Approval, AuditLog, ProcurementRequest, PurchaseOrder, User
from app.models.enums import ActorType, ApprovalDecision, PurchaseOrderStatus as PO, UserRole, WorkflowState as S
from app.workflow import Actor, WorkflowError, transition

router = APIRouter(prefix="/purchase-orders", tags=["purchase orders"])


async def _load(db: AsyncSession, po_id: uuid.UUID, user: User, *, lock: bool = False) -> tuple[PurchaseOrder, ProcurementRequest]:
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == po_id).options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.vendor))
    if lock:
        stmt = stmt.with_for_update(of=PurchaseOrder)
    po = await db.scalar(stmt)
    pr = await db.get(ProcurementRequest, po.request_id) if po else None
    if not po or not can_view(user, pr):
        raise HTTPException(404, "Purchase order not found")
    return po, pr


async def _out(db: AsyncSession, po: PurchaseOrder, pr: ProcurementRequest) -> PurchaseOrderOut:
    approvals = (await db.scalars(select(Approval).where(Approval.purchase_order_id == po.id).order_by(Approval.decided_at))).all()
    out = PurchaseOrderOut.model_validate(po, from_attributes=True)
    return out.model_copy(update={"vendor_name": po.vendor.name, "request_status": pr.status,
                                  "approvals": [a for a in approvals]})


@router.get("", response_model=list[PurchaseOrderOut])
async def list_purchase_orders(status: PO | None = None, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    stmt = (select(PurchaseOrder).join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id)
            .options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.vendor), selectinload(PurchaseOrder.request))
            .order_by(PurchaseOrder.created_at.desc()))
    if user.role == UserRole.REQUESTER:
        stmt = stmt.where(ProcurementRequest.requester_id == user.id)
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    pos = (await db.scalars(stmt)).all()
    return [PurchaseOrderOut.model_validate(p, from_attributes=True).model_copy(update={"vendor_name": p.vendor.name, "request_status": p.request.status})
            for p in pos]


@router.get("/{po_id}", response_model=PurchaseOrderOut)
async def get_purchase_order(po_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    po, pr = await _load(db, po_id, user)
    return await _out(db, po, pr)


async def _decide(db: AsyncSession, po_id: uuid.UUID, user: User, decision: ApprovalDecision, reason: str | None) -> PurchaseOrderOut:
    po, pr = await _load(db, po_id, user, lock=True)
    if pr.requester_id == user.id:
        raise HTTPException(403, "Requesters cannot approve or reject their own purchase")
    if po.status != PO.PENDING_APPROVAL or pr.status != S.AWAITING_APPROVAL:
        raise HTTPException(409, f"Purchase order is {po.status} and its request is {pr.status}; only PENDING_APPROVAL orders awaiting approval can be decided")

    db.add(Approval(purchase_order_id=po.id, approver_id=user.id, decision=decision, reason=reason))
    po.status = PO.APPROVED if decision == ApprovalDecision.APPROVED else PO.REJECTED
    db.add(AuditLog(actor_type=ActorType.USER, actor_user_id=user.id, action=f"purchase_order.{decision.lower()}", entity_type="purchase_order",
                    entity_id=po.id, details={"po_number": po.po_number, "total": str(po.total), "reason": reason}))
    await db.flush()  # the approval row must exist before the state machine's approval guard looks for it
    try:
        await transition(db, pr.id, S.APPROVED if decision == ApprovalDecision.APPROVED else S.REJECTED,
                         actor=Actor.user(user.id), reason=reason or f"{decision.title()} by {user.full_name}",
                         payload={"purchase_order_id": str(po.id), "po_number": po.po_number}, commit=False)
    except WorkflowError as e:
        await db.rollback()
        raise HTTPException(409, str(e)) from e
    await db.commit()
    await db.refresh(pr)
    return await _out(db, po, pr)


@router.post("/{po_id}/approve", response_model=PurchaseOrderOut)
async def approve(po_id: uuid.UUID, body: ApproveIn | None = None, db: AsyncSession = Depends(get_session),
                  user: User = Depends(require_role(UserRole.APPROVER, UserRole.ADMIN))):
    return await _decide(db, po_id, user, ApprovalDecision.APPROVED, body.note if body else None)


@router.post("/{po_id}/reject", response_model=PurchaseOrderOut)
async def reject(po_id: uuid.UUID, body: RejectIn, db: AsyncSession = Depends(get_session),
                 user: User = Depends(require_role(UserRole.APPROVER, UserRole.ADMIN))):
    return await _decide(db, po_id, user, ApprovalDecision.REJECTED, body.reason)
