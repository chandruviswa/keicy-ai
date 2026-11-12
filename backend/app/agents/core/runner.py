"""Runs one agent: persists an AgentRun, enforces the timeout, emits events, never raises."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.core.base import AgentContext, AgentResult, BaseAgent
from app.agents.core.tools import ToolContext, ToolRegistry
from app.ai.errors import LLMGatewayError
from app.ai.gateway import LLMGateway, get_gateway
from app.models import AgentRun, WorkflowEvent
from app.models.enums import RunStatus

log = logging.getLogger("procura.agents")


class AgentRunner:
    def __init__(self, *, sessions: async_sessionmaker[AsyncSession] | None = None, gateway: LLMGateway | None = None,
                 registry: ToolRegistry | None = None) -> None:
        if sessions is None:
            from app.db.session import SessionLocal

            sessions = SessionLocal
        if registry is None:
            from app.tools import registry as default_registry

            registry = default_registry
        self.sessions = sessions
        self._gateway = gateway
        self.registry = registry

    @property
    def gateway(self) -> LLMGateway:
        return self._gateway or get_gateway()

    async def run(self, agent: BaseAgent, request_id: uuid.UUID, input: dict[str, Any] | None = None) -> AgentResult:
        input = input or {}
        started = time.perf_counter()

        async with self.sessions() as db:
            run = AgentRun(request_id=request_id, agent_name=agent.name, status=RunStatus.RUNNING, input=input,
                           started_at=datetime.now(timezone.utc))
            db.add(run)
            await db.flush()
            db.add(WorkflowEvent(request_id=request_id, event_type="agent.started",
                                 payload={"agent": agent.name, "agent_run_id": str(run.id)}))
            await db.commit()
            run_id = run.id

        status, output, error = RunStatus.FAILED, None, None
        async with self.sessions() as work_db:
            tool_ctx = ToolContext(db=work_db, request_id=request_id, agent_name=agent.name, agent_run_id=run_id,
                                   allowed_tools=agent.allowed_tools, max_tool_calls=agent.max_tool_calls, log_sessions=self.sessions)
            ctx = AgentContext(agent=agent, request_id=request_id, agent_run_id=run_id, input=input, tool_ctx=tool_ctx,
                               registry=self.registry, gateway=self.gateway)
            try:
                out = await asyncio.wait_for(agent.run(ctx), timeout=agent.timeout_s)
                output = out.model_dump(mode="json") if isinstance(out, BaseModel) else out
                if ctx.notes:
                    output = {**(output or {}), "_notes": ctx.notes}
                await work_db.commit()
                status = RunStatus.SUCCEEDED
            except TimeoutError:
                await work_db.rollback()
                status, error = RunStatus.TIMED_OUT, f"{agent.name} exceeded its {agent.timeout_s}s limit"
            except LLMGatewayError as e:
                await work_db.rollback()
                error = str(e)
            except Exception as e:  # noqa: BLE001 — agent failures become a FAILED run, never a crash
                await work_db.rollback()
                log.exception("Agent %s failed for request %s", agent.name, request_id)
                error = f"{type(e).__name__}: {e}"
            calls = tool_ctx.calls_made

        duration = round((time.perf_counter() - started) * 1000)
        async with self.sessions() as db:
            run = await db.get(AgentRun, run_id)
            run.status, run.output, run.error, run.finished_at = status, output, error, datetime.now(timezone.utc)
            event = "agent.completed" if status == RunStatus.SUCCEEDED else "agent.failed"
            db.add(WorkflowEvent(request_id=request_id, event_type=event, payload={
                "agent": agent.name, "agent_run_id": str(run_id), "status": status, "duration_ms": duration,
                "tool_calls": calls, "error": error}))
            await db.commit()

        return AgentResult(agent=agent.name, agent_run_id=run_id, status=status, output=output, error=error,
                           duration_ms=duration, tool_calls=calls)
