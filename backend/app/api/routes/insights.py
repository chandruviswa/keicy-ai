"""Read-only views for the Approval Center, Payment Center and Intelligence & Audit screens."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Float, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_visible_request, require_role
from app.db.session import get_session
from app.models import (
    AgentRun, AgentToolLog, Approval, AuditLog, ModelExecution, ProcurementRequest, PurchaseOrder, User, Vendor, WorkflowStateHistory,
)
from app.models.enums import RunStatus, ToolCallStatus, UserRole

router = APIRouter(tags=["insights"])
REVIEWERS = require_role(UserRole.APPROVER, UserRole.ADMIN)


def _scope(user: User):
    """Requesters see their own requests; approvers and admins see everything."""
    return ProcurementRequest.requester_id == user.id if user.role == UserRole.REQUESTER else True


# ---------- approvals ----------

class ApprovalRow(BaseModel):
    id: uuid.UUID
    decision: str
    reason: str | None
    decided_at: datetime
    approver_name: str
    purchase_order_id: uuid.UUID
    po_number: str
    total: Decimal
    currency: str
    vendor_name: str
    request_id: uuid.UUID
    request_title: str | None


@router.get("/approvals", response_model=list[ApprovalRow])
async def approval_history(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    rows = (await db.execute(
        select(Approval, User.full_name, PurchaseOrder, Vendor.name, ProcurementRequest.title)
        .join(User, User.id == Approval.approver_id)
        .join(PurchaseOrder, PurchaseOrder.id == Approval.purchase_order_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id)
        .where(_scope(user)).order_by(Approval.decided_at.desc()).limit(limit)
    )).all()
    return [ApprovalRow(id=a.id, decision=a.decision, reason=a.reason, decided_at=a.decided_at, approver_name=name, purchase_order_id=po.id,
                        po_number=po.po_number, total=po.total, currency=po.currency, vendor_name=vendor, request_id=po.request_id,
                        request_title=title) for a, name, po, vendor, title in rows]


# ---------- workflow history ----------

class HistoryRow(BaseModel):
    from_state: str | None
    to_state: str
    reason: str | None
    actor: str
    created_at: datetime


@router.get("/procurement/requests/{request_id}/history", response_model=list[HistoryRow])
async def request_history(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    await get_visible_request(db, request_id, user)
    rows = (await db.execute(
        select(WorkflowStateHistory, User.full_name).outerjoin(User, User.id == WorkflowStateHistory.actor_user_id)
        .where(WorkflowStateHistory.request_id == request_id).order_by(WorkflowStateHistory.created_at)
    )).all()
    return [HistoryRow(from_state=h.from_state, to_state=h.to_state, reason=h.reason, created_at=h.created_at,
                       actor=name or (f"{h.actor_agent} agent" if h.actor_agent else "system")) for h, name in rows]


# ---------- agent runs ----------

class RunRow(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    request_title: str | None
    agent_name: str
    status: str
    error: str | None
    duration_ms: int | None
    tool_calls: int
    started_at: datetime | None


@router.get("/agents/runs", response_model=list[RunRow])
async def list_runs(agent: str | None = None, status: str | None = None, limit: int = Query(50, ge=1, le=200),
                    db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    tools = select(AgentToolLog.agent_run_id, func.count().label("n")).group_by(AgentToolLog.agent_run_id).subquery()
    stmt = (select(AgentRun, ProcurementRequest.title, func.coalesce(tools.c.n, 0))
            .join(ProcurementRequest, ProcurementRequest.id == AgentRun.request_id)
            .outerjoin(tools, tools.c.agent_run_id == AgentRun.id)
            .where(_scope(user)).order_by(AgentRun.created_at.desc()).limit(limit))
    if agent:
        stmt = stmt.where(AgentRun.agent_name == agent)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    out = []
    for r, title, n in (await db.execute(stmt)).all():
        dur = round((r.finished_at - r.started_at).total_seconds() * 1000) if r.finished_at and r.started_at else None
        out.append(RunRow(id=r.id, request_id=r.request_id, request_title=title, agent_name=r.agent_name, status=r.status, error=r.error,
                          duration_ms=dur, tool_calls=n, started_at=r.started_at))
    return out


# ---------- intelligence summary ----------

@router.get("/intelligence/summary")
async def intelligence_summary(db: AsyncSession = Depends(get_session), user: User = Depends(REVIEWERS)) -> dict[str, Any]:
    def rate(ok, total):
        return round(float(ok) / float(total), 3) if total else None

    models = (await db.execute(
        select(ModelExecution.provider, ModelExecution.model, func.count(), func.sum(case((ModelExecution.success, 1), else_=0)),
               func.sum(case((ModelExecution.fallback_used, 1), else_=0)), func.avg(ModelExecution.latency_ms),
               func.percentile_cont(0.95).within_group(ModelExecution.latency_ms),
               func.coalesce(func.sum(ModelExecution.input_tokens), 0), func.coalesce(func.sum(ModelExecution.output_tokens), 0))
        .group_by(ModelExecution.provider, ModelExecution.model).order_by(func.count().desc())
    )).all()
    errors = (await db.execute(
        select(ModelExecution.provider, ModelExecution.error_type, func.count()).where(ModelExecution.error_type.is_not(None))
        .group_by(ModelExecution.provider, ModelExecution.error_type)
    )).all()
    tools = (await db.execute(
        select(AgentToolLog.tool_name, func.count(), func.sum(case((AgentToolLog.status == ToolCallStatus.SUCCEEDED, 1), else_=0)),
               func.sum(case((AgentToolLog.status == ToolCallStatus.BLOCKED, 1), else_=0)),
               func.sum(case((AgentToolLog.status == ToolCallStatus.DUPLICATE, 1), else_=0)), func.avg(AgentToolLog.duration_ms))
        .group_by(AgentToolLog.tool_name).order_by(func.count().desc())
    )).all()
    duration = func.extract("epoch", AgentRun.finished_at - AgentRun.started_at) * 1000
    agents = (await db.execute(
        select(AgentRun.agent_name, func.count(), func.sum(case((AgentRun.status == RunStatus.SUCCEEDED, 1), else_=0)),
               func.sum(case((AgentRun.status == RunStatus.TIMED_OUT, 1), else_=0)), func.avg(cast(duration, Float)))
        .where(and_(AgentRun.finished_at.is_not(None)))
        .group_by(AgentRun.agent_name)
    )).all()
    order = ["intent", "research", "evaluation", "procurement", "finance", "reconciliation"]
    return {
        "models": [{"provider": p, "model": m, "calls": n, "success_rate": rate(ok, n), "fallback_calls": int(fb or 0),
                    "avg_latency_ms": round(float(avg or 0)), "p95_latency_ms": round(float(p95 or 0)), "input_tokens": int(ti), "output_tokens": int(to)}
                   for p, m, n, ok, fb, avg, p95, ti, to in models],
        "model_errors": [{"provider": p, "error_type": e, "count": n} for p, e, n in errors],
        "tools": [{"tool": t, "calls": n, "success_rate": rate(ok, n), "blocked": int(b or 0), "duplicates": int(d or 0), "avg_ms": round(float(avg or 0), 1)}
                  for t, n, ok, b, d, avg in tools],
        "agents": sorted([{"agent": a, "runs": n, "success_rate": rate(ok, n), "timeouts": int(to or 0), "avg_duration_ms": round(float(avg or 0))}
                          for a, n, ok, to, avg in agents], key=lambda x: order.index(x["agent"]) if x["agent"] in order else 99),
    }


# ---------- audit trail ----------

class AuditRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    actor_type: str
    actor: str
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    details: dict[str, Any]


@router.get("/audit/logs", response_model=list[AuditRow])
async def audit_logs(action: str | None = Query(None, description="Prefix match, e.g. 'payment.'"), entity_type: str | None = None,
                     entity_id: uuid.UUID | None = None, limit: int = Query(100, ge=1, le=500), before: datetime | None = None,
                     db: AsyncSession = Depends(get_session), user: User = Depends(REVIEWERS)):
    stmt = select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.actor_user_id).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if before:
        stmt = stmt.where(AuditLog.created_at < before)
    return [AuditRow(id=a.id, created_at=a.created_at, actor_type=a.actor_type, actor=name or a.actor_name or a.actor_type, action=a.action,
                     entity_type=a.entity_type, entity_id=a.entity_id, details=a.details) for a, name in (await db.execute(stmt)).all()]
