"""Restart recovery for requests that were mid-workflow when the backend stopped.

Parts 3–4 register a resumer per state (e.g. "continue the agent pipeline from RESEARCHING").
A request with no resumer for its state is moved to FAILED with a clear reason, so nothing
stays silently stuck. Requests can be re-run from FAILED.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ProcurementRequest
from app.models.enums import WorkflowState
from app.workflow.machine import Actor, WorkflowError, transition
from app.workflow.states import AGENT_ACTIVE

log = logging.getLogger("procura.workflow")

Resumer = Callable[[uuid.UUID, WorkflowState], Awaitable[None]]
_resumers: dict[WorkflowState, Resumer] = {}


def register_resumer(states: set[WorkflowState] | frozenset[WorkflowState], fn: Resumer) -> None:
    for s in states:
        _resumers[s] = fn


def clear_resumers() -> None:
    _resumers.clear()


@dataclass
class RecoveryReport:
    resumed: list[uuid.UUID] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    errors: dict[uuid.UUID, str] = field(default_factory=dict)


async def recover_interrupted(sessions: async_sessionmaker[AsyncSession]) -> RecoveryReport:
    report = RecoveryReport()
    async with sessions() as db:
        stuck = (await db.execute(
            select(ProcurementRequest.id, ProcurementRequest.status).where(ProcurementRequest.status.in_(list(AGENT_ACTIVE | set(_resumers))))
        )).all()

    for request_id, status in stuck:
        state = WorkflowState(status)
        resumer = _resumers.get(state)
        try:
            if resumer:
                await resumer(request_id, state)
                report.resumed.append(request_id)
            else:
                async with sessions() as db:
                    await transition(db, request_id, WorkflowState.FAILED, actor=Actor.system("recovery"),
                                     reason=f"Interrupted in {state} by a backend restart; no resumer available")
                report.failed.append(request_id)
        except (WorkflowError, Exception) as e:  # noqa: BLE001 — one bad request must not block the rest
            log.exception("Recovery failed for request %s", request_id)
            report.errors[request_id] = str(e)

    if stuck:
        log.info("Workflow recovery: resumed=%d failed=%d errors=%d", len(report.resumed), len(report.failed), len(report.errors))
    return report
