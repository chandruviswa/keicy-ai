import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import can_view, get_current_user
from app.api.schemas import AgentRunLogsOut, AgentRunOut, AgentRunSummary
from app.db.session import get_session
from app.models import AgentRun, AgentToolLog, ModelExecution, ProcurementRequest, User

router = APIRouter(prefix="/agents", tags=["agents"])


async def _visible_run(db: AsyncSession, run_id: uuid.UUID, user: User) -> AgentRun:
    run = await db.get(AgentRun, run_id)
    pr = await db.get(ProcurementRequest, run.request_id) if run else None
    if not run or not can_view(user, pr):
        raise HTTPException(404, "Agent run not found")
    return run


@router.get("/runs/{run_id}", response_model=AgentRunOut)
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    return await _visible_run(db, run_id, user)


@router.get("/runs/{run_id}/logs", response_model=AgentRunLogsOut)
async def get_run_logs(run_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    run = await _visible_run(db, run_id, user)
    tools = (await db.scalars(select(AgentToolLog).where(AgentToolLog.agent_run_id == run_id).order_by(AgentToolLog.created_at))).all()
    models = (await db.scalars(select(ModelExecution).where(ModelExecution.agent_run_id == run_id).order_by(ModelExecution.created_at))).all()
    return AgentRunLogsOut(run=AgentRunSummary.model_validate(run), tool_calls=list(tools), model_calls=list(models))
