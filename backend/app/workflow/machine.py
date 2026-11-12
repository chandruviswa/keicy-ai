"""The only code allowed to change ProcurementRequest.status.

transition() locks the request row, checks the transition table and guards, then writes the
new status, a state-history row, a workflow event and an audit entry in one DB transaction.
Assigning `request.status = ...` anywhere else raises DirectStatusWriteError.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Approval, AuditLog, ProcurementRequest, PurchaseOrder, WorkflowEvent, WorkflowStateHistory
from app.models.enums import ActorType, ApprovalDecision, WorkflowState
from app.workflow.states import REQUIRES_APPROVAL, TERMINAL, allowed_next


class WorkflowError(Exception):
    pass


class InvalidTransitionError(WorkflowError):
    def __init__(self, from_state: str, to_state: str) -> None:
        allowed = ", ".join(sorted(allowed_next(from_state))) or "none (terminal state)"
        super().__init__(f"Cannot move from {from_state} to {to_state}. Allowed: {allowed}")
        self.from_state, self.to_state = from_state, to_state


class ApprovalRequiredError(WorkflowError):
    pass


class DirectStatusWriteError(WorkflowError):
    pass


class RequestNotFoundError(WorkflowError):
    pass


@dataclass(frozen=True)
class Actor:
    type: ActorType
    user_id: uuid.UUID | None = None
    name: str | None = None  # agent name, or "system"

    @classmethod
    def user(cls, user_id: uuid.UUID) -> Actor:
        return cls(ActorType.USER, user_id=user_id)

    @classmethod
    def agent(cls, name: str) -> Actor:
        return cls(ActorType.AGENT, name=name)

    @classmethod
    def system(cls, name: str = "system") -> Actor:
        return cls(ActorType.SYSTEM, name=name)


# ---------- direct-write guard ----------

_in_transition: ContextVar[bool] = ContextVar("_in_transition", default=False)


@contextmanager
def _transition_scope():
    token = _in_transition.set(True)
    try:
        yield
    finally:
        _in_transition.reset(token)


@event.listens_for(ProcurementRequest.status, "set", propagate=True)
def _block_direct_status_writes(target, value, oldvalue, initiator):
    # New, never-saved requests may set their initial status; saved ones must go through transition().
    if inspect(target).persistent and not _in_transition.get():
        raise DirectStatusWriteError("Change ProcurementRequest.status only via app.workflow.transition()")


# ---------- guards ----------

async def has_approval(db: AsyncSession, request_id: uuid.UUID) -> bool:
    """True if the most recent approval decision on this request's purchase orders is APPROVED."""
    latest = await db.scalar(
        select(Approval.decision)
        .join(PurchaseOrder, PurchaseOrder.id == Approval.purchase_order_id)
        .where(PurchaseOrder.request_id == request_id)
        .order_by(Approval.decided_at.desc(), Approval.created_at.desc())
        .limit(1)
    )
    return latest == ApprovalDecision.APPROVED


# ---------- transition ----------

async def transition(
    db: AsyncSession,
    request_id: uuid.UUID,
    to_state: WorkflowState | str,
    *,
    actor: Actor,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> ProcurementRequest:
    to_state = WorkflowState(to_state)

    # Row lock: concurrent transitions on one request are serialised, and the second one sees the new state.
    pr = await db.scalar(
        select(ProcurementRequest).where(ProcurementRequest.id == request_id).with_for_update().execution_options(populate_existing=True)
    )
    if pr is None:
        raise RequestNotFoundError(f"Procurement request {request_id} not found")

    from_state = WorkflowState(pr.status)
    if to_state not in allowed_next(from_state):
        raise InvalidTransitionError(from_state, to_state)
    if to_state in REQUIRES_APPROVAL and not await has_approval(db, request_id):
        raise ApprovalRequiredError(f"{to_state} requires an approved purchase order — none recorded for request {request_id}")

    with _transition_scope():
        pr.status = to_state

    details = {"from": from_state, "to": to_state, "reason": reason, **(payload or {})}
    db.add(WorkflowStateHistory(request_id=pr.id, from_state=from_state, to_state=to_state, reason=reason,
                                actor_user_id=actor.user_id, actor_agent=actor.name if actor.type == ActorType.AGENT else None))
    db.add(WorkflowEvent(request_id=pr.id, event_type="workflow.state_changed", payload=details))
    db.add(AuditLog(actor_type=actor.type, actor_user_id=actor.user_id, actor_name=actor.name, action="workflow.transition",
                    entity_type="procurement_request", entity_id=pr.id, details=details))
    if to_state in TERMINAL:
        db.add(WorkflowEvent(request_id=pr.id, event_type=f"workflow.{to_state.lower()}", payload=details))

    if commit:
        await db.commit()
    else:
        await db.flush()
    return pr
