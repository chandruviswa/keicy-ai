import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.pipeline import executor
from app.api.deps import get_current_user, get_visible_request
from app.api.schemas import (
    AgentRunSummary, ProcurementRequestIn, ProcurementRequestOut, ProcurementRequestUpdate, WorkflowStatusOut,
)
from app.db.session import get_session
from app.models import AgentRun, AuditLog, ProcurementRequest, PurchaseOrder, User, WorkflowStateHistory
from app.models.enums import ActorType, RunStatus, UserRole, WorkflowState as S
from app.workflow import Actor, WorkflowError, allowed_next, transition
from app.workflow.events import emit

router = APIRouter(prefix="/procurement", tags=["procurement"])

EDITABLE = {S.CREATED, S.FAILED}
EXECUTABLE = {S.CREATED, S.FAILED}


def _own(pr: ProcurementRequest, user: User) -> None:
    if pr.requester_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(403, "Only the requester can do this")


@router.post("/requests", response_model=ProcurementRequestOut, status_code=201)
async def create_request(body: ProcurementRequestIn, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    pr = ProcurementRequest(requester_id=user.id, status=S.CREATED, **body.model_dump())
    db.add(pr)
    await db.flush()
    db.add(WorkflowStateHistory(request_id=pr.id, from_state=None, to_state=S.CREATED, actor_user_id=user.id, reason="Request submitted"))
    db.add(AuditLog(actor_type=ActorType.USER, actor_user_id=user.id, action="procurement_request.created", entity_type="procurement_request",
                    entity_id=pr.id, details={"title": pr.title}))
    await db.commit()
    await db.refresh(pr)
    return pr


@router.get("/requests", response_model=list[ProcurementRequestOut])
async def list_requests(db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    stmt = select(ProcurementRequest).order_by(ProcurementRequest.created_at.desc())
    if user.role == UserRole.REQUESTER:
        stmt = stmt.where(ProcurementRequest.requester_id == user.id)
    return (await db.scalars(stmt)).all()


@router.get("/requests/{request_id}", response_model=ProcurementRequestOut)
async def get_request(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    return await get_visible_request(db, request_id, user)


@router.patch("/requests/{request_id}", response_model=ProcurementRequestOut)
async def update_request(request_id: uuid.UUID, body: ProcurementRequestUpdate, db: AsyncSession = Depends(get_session),
                         user: User = Depends(get_current_user)):
    pr = await get_visible_request(db, request_id, user)
    _own(pr, user)
    if pr.status not in EDITABLE:
        raise HTTPException(409, f"A request can only be edited while {' or '.join(sorted(EDITABLE))} (it is {pr.status})")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(pr, k, v)
    db.add(AuditLog(actor_type=ActorType.USER, actor_user_id=user.id, action="procurement_request.updated", entity_type="procurement_request",
                    entity_id=pr.id, details={k: str(v) for k, v in changes.items()}))
    await db.commit()
    await db.refresh(pr)
    return pr


@router.post("/requests/{request_id}/execute", response_model=WorkflowStatusOut, status_code=status.HTTP_202_ACCEPTED)
async def execute_request(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    """Start (or re-run after FAILED) the agent pipeline. Returns at once; follow progress over the WebSocket."""
    pr = await get_visible_request(db, request_id, user)
    _own(pr, user)
    if executor.is_running(request_id):
        raise HTTPException(409, "This request is already being processed")
    if pr.status not in EXECUTABLE:
        raise HTTPException(409, f"Only {' or '.join(sorted(EXECUTABLE))} requests can be executed (it is {pr.status})")
    try:
        await transition(db, request_id, S.ANALYZING, actor=Actor.user(user.id), reason="Execution started", commit=False)
        await emit(db, request_id, "workflow.started", {"started_by": str(user.id)})
        await db.commit()
    except WorkflowError as e:  # lost a race with another execute call
        raise HTTPException(409, str(e)) from e
    executor.start(request_id, S.ANALYZING)
    return await _status(db, request_id)


@router.post("/requests/{request_id}/cancel", response_model=WorkflowStatusOut)
async def cancel_request(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    pr = await get_visible_request(db, request_id, user)
    _own(pr, user)
    try:
        await transition(db, request_id, S.CANCELLED, actor=Actor.user(user.id), reason="Cancelled by requester")
    except WorkflowError as e:
        raise HTTPException(409, str(e)) from e
    # A running pipeline notices the state change before its next step and stops.
    return await _status(db, request_id)


@router.get("/requests/{request_id}/status", response_model=WorkflowStatusOut)
async def request_status(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    await get_visible_request(db, request_id, user)
    return await _status(db, request_id)


@router.get("/requests/{request_id}/runs", response_model=list[AgentRunSummary])
async def request_runs(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    await get_visible_request(db, request_id, user)
    return (await db.scalars(select(AgentRun).where(AgentRun.request_id == request_id).order_by(AgentRun.created_at))).all()


@router.get("/requests/{request_id}/offers")
async def request_offers(request_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    """What the agents found: matched products and quotes (research), ranking and explanation (evaluation),
    the drafted PO (procurement) and the finance report — from the latest successful run of each agent."""
    await get_visible_request(db, request_id, user)
    latest = await _latest_outputs(db, request_id)
    research, evaluation = latest.get("research") or {}, latest.get("evaluation") or {}
    return {
        "requirement": (latest.get("intent") or {}).get("requirement"),
        "items": research.get("items", []),
        "ranking": evaluation.get("ranking", []),
        "recommended": evaluation.get("recommended"),
        "excluded": evaluation.get("excluded", []),
        "explanation": evaluation.get("explanation"),
        "explanation_source": evaluation.get("explanation_source"),
        "purchase_order": (latest.get("procurement") or {}).get("purchase_order"),
        "po_summary": (latest.get("procurement") or {}).get("summary"),
        "finance_report": (latest.get("finance") or {}).get("report"),
    }


async def _latest_outputs(db: AsyncSession, request_id: uuid.UUID) -> dict[str, dict]:
    runs = (await db.scalars(select(AgentRun).where(AgentRun.request_id == request_id, AgentRun.status == RunStatus.SUCCEEDED)
                             .order_by(AgentRun.created_at))).all()
    return {r.agent_name: r.output for r in runs}


async def _status(db: AsyncSession, request_id: uuid.UUID) -> WorkflowStatusOut:
    db.expire_all()
    pr = await db.get(ProcurementRequest, request_id)
    last = await db.scalar(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == request_id)
                           .order_by(WorkflowStateHistory.created_at.desc()).limit(1))
    runs = (await db.scalars(select(AgentRun).where(AgentRun.request_id == request_id).order_by(AgentRun.created_at))).all()
    intent = next((r.output for r in reversed(runs) if r.agent_name == "intent" and r.output), None) or {}
    questions = intent.get("clarification_questions", []) if intent.get("outcome") == "clarify" and pr.status == S.FAILED else []
    po_id = await db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.request_id == request_id).order_by(PurchaseOrder.created_at.desc()).limit(1))
    return WorkflowStatusOut(
        id=pr.id, status=pr.status, is_running=executor.is_running(request_id), allowed_next=sorted(allowed_next(pr.status)),
        last_reason=last.reason if last else None, clarification_questions=questions, purchase_order_id=po_id,
        runs=[AgentRunSummary.model_validate(r) for r in runs],
    )
