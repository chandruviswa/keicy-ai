"""Workflow state machine: allowed transitions, approval guard, persistence, concurrency, recovery."""

import asyncio
import uuid
from collections import deque
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import SessionLocal
from app.models import Approval, AuditLog, ProcurementRequest, PurchaseOrder, User, Vendor, WorkflowEvent, WorkflowStateHistory
from app.models.enums import WorkflowState as S
from app.workflow import (
    AGENT_ACTIVE, TERMINAL, TRANSITIONS, Actor, ApprovalRequiredError, DirectStatusWriteError, InvalidTransitionError,
    has_approval, recover_interrupted, register_resumer, transition,
)
from app.workflow.recovery import clear_resumers
from tests.conftest import TEST_URL

SYS = Actor.system("test")
TO_APPROVAL = [S.ANALYZING, S.RESEARCHING, S.EVALUATING, S.ORDER_DRAFTED, S.FINANCE_VALIDATING, S.AWAITING_APPROVAL]


async def walk(db, request_id, states):
    for s in states:
        await transition(db, request_id, s, actor=SYS)


async def status_of(request_id) -> str:
    async with SessionLocal() as s:
        return await s.scalar(select(ProcurementRequest.status).where(ProcurementRequest.id == request_id))


async def add_decision(db, request_id, decision: str, reason: str | None = None) -> None:
    vendor = await db.scalar(select(Vendor).limit(1))
    approver = await db.scalar(select(User).where(User.role == "approver"))
    po = await db.scalar(select(PurchaseOrder).where(PurchaseOrder.request_id == request_id))
    if po is None:
        po = PurchaseOrder(po_number=f"PO-T-{uuid.uuid4().hex[:8]}", request_id=request_id, vendor_id=vendor.id, currency="USD",
                           subtotal=Decimal("100.00"), tax_amount=Decimal("0.00"), total=Decimal("100.00"))
        db.add(po)
        await db.flush()
    db.add(Approval(purchase_order_id=po.id, approver_id=approver.id, decision=decision, reason=reason))
    await db.commit()


# ---------- the graph itself ----------

def test_every_state_reachable_from_created():
    seen, todo = {S.CREATED}, deque([S.CREATED])
    while todo:
        for nxt in TRANSITIONS[todo.popleft()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    assert seen == set(S)


def test_terminal_states_and_payment_safety():
    assert TERMINAL == {S.COMPLETED, S.REJECTED, S.CANCELLED}
    assert S.CANCELLED not in TRANSITIONS[S.PAYMENT_PROCESSING], "no cancelling while PayPal is capturing"
    # Payment states are only reachable via APPROVED.
    for state, nxt in TRANSITIONS.items():
        if S.PAYMENT_PENDING in nxt:
            assert state in {S.APPROVED, S.PAYMENT_FAILED}


# ---------- transitions ----------

async def test_happy_path_writes_history_events_and_audit(db, request_id):
    await walk(db, request_id, TO_APPROVAL)
    assert await status_of(request_id) == S.AWAITING_APPROVAL

    hist = (await db.scalars(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == request_id).order_by(WorkflowStateHistory.created_at))).all()
    assert [h.to_state for h in hist] == TO_APPROVAL
    assert hist[0].from_state == S.CREATED and all(h.from_state != h.to_state for h in hist)
    events = await db.scalar(select(text("count(*)")).select_from(WorkflowEvent).where(WorkflowEvent.request_id == request_id, WorkflowEvent.event_type == "workflow.state_changed"))
    audits = await db.scalar(select(text("count(*)")).select_from(AuditLog).where(AuditLog.entity_id == request_id, AuditLog.action == "workflow.transition"))
    assert events == audits == len(TO_APPROVAL)


async def test_invalid_transition_rejected_and_state_unchanged(db, request_id):
    with pytest.raises(InvalidTransitionError, match="Allowed: ANALYZING, CANCELLED, FAILED"):
        await transition(db, request_id, S.EVALUATING, actor=SYS)
    await db.rollback()
    assert await status_of(request_id) == S.CREATED


async def test_terminal_state_has_no_exit(db, request_id):
    await transition(db, request_id, S.CANCELLED, actor=SYS)
    for target in (S.ANALYZING, S.CREATED, S.APPROVED):
        with pytest.raises(InvalidTransitionError, match="terminal"):
            await transition(db, request_id, target, actor=SYS)
        await db.rollback()


async def test_failed_can_be_rerun(db, request_id):
    await walk(db, request_id, [S.ANALYZING, S.FAILED, S.ANALYZING])
    assert await status_of(request_id) == S.ANALYZING


async def test_direct_status_write_is_blocked(db, request_id):
    pr = await db.get(ProcurementRequest, request_id)
    with pytest.raises(DirectStatusWriteError):
        pr.status = S.APPROVED


async def test_actor_is_recorded(db, request_id):
    user = await db.scalar(select(User).where(User.role == "requester"))
    await transition(db, request_id, S.ANALYZING, actor=Actor.agent("intent"))
    await transition(db, request_id, S.CANCELLED, actor=Actor.user(user.id), reason="no longer needed")
    hist = (await db.scalars(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == request_id).order_by(WorkflowStateHistory.created_at))).all()
    assert (hist[0].actor_agent, hist[0].actor_user_id) == ("intent", None)
    assert (hist[1].actor_user_id, hist[1].reason) == (user.id, "no longer needed")
    assert await db.scalar(select(text("count(*)")).select_from(WorkflowEvent).where(WorkflowEvent.request_id == request_id, WorkflowEvent.event_type == "workflow.cancelled")) == 1


# ---------- approval guard ----------

async def test_cannot_approve_without_recorded_approval(db, request_id):
    await walk(db, request_id, TO_APPROVAL)
    with pytest.raises(ApprovalRequiredError):
        await transition(db, request_id, S.APPROVED, actor=SYS)
    await db.rollback()
    await add_decision(db, request_id, "APPROVED")
    await walk(db, request_id, [S.APPROVED, S.PAYMENT_PENDING, S.PAYMENT_PROCESSING, S.PAYMENT_COMPLETED, S.RECONCILING, S.COMPLETED])
    assert await status_of(request_id) == S.COMPLETED


async def test_payment_blocked_even_if_status_is_tampered(db, request_id):
    # Simulate someone forcing the column directly in SQL, bypassing the state machine.
    await db.execute(text("UPDATE procurement_requests SET status = 'APPROVED' WHERE id = :id"), {"id": request_id})
    await db.commit()
    with pytest.raises(ApprovalRequiredError):
        await transition(db, request_id, S.PAYMENT_PENDING, actor=SYS)


async def test_latest_rejection_overrides_earlier_approval(db, request_id):
    await add_decision(db, request_id, "APPROVED")
    assert await has_approval(db, request_id)
    await add_decision(db, request_id, "REJECTED", reason="vendor changed terms")
    assert not await has_approval(db, request_id)


# ---------- persistence & concurrency ----------

async def test_state_survives_a_fresh_engine(db, request_id):
    await walk(db, request_id, [S.ANALYZING, S.RESEARCHING])
    engine = create_async_engine(TEST_URL, poolclass=NullPool)  # stands in for a restarted backend
    try:
        async with async_sessionmaker(engine)() as fresh:
            assert await fresh.scalar(select(ProcurementRequest.status).where(ProcurementRequest.id == request_id)) == S.RESEARCHING
            n = await fresh.scalar(select(text("count(*)")).select_from(WorkflowStateHistory).where(WorkflowStateHistory.request_id == request_id))
            assert n == 2
    finally:
        await engine.dispose()


async def test_concurrent_transitions_are_serialised(request_id):
    async def attempt():
        async with SessionLocal() as s:
            await transition(s, request_id, S.ANALYZING, actor=SYS)

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    assert sum(r is None for r in results) == 1
    assert sum(isinstance(r, InvalidTransitionError) for r in results) == 1
    assert await status_of(request_id) == S.ANALYZING


# ---------- recovery ----------

async def test_recovery_fails_stuck_requests_without_resumer(db, request_id):
    clear_resumers()
    await walk(db, request_id, [S.ANALYZING, S.RESEARCHING])
    report = await recover_interrupted(SessionLocal)
    assert request_id in report.failed
    assert await status_of(request_id) == S.FAILED
    last = await db.scalar(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == request_id).order_by(WorkflowStateHistory.created_at.desc()).limit(1))
    assert "Interrupted in RESEARCHING" in last.reason


async def test_recovery_calls_registered_resumer(db, request_id):
    clear_resumers()
    calls = []

    async def resume(rid, state):
        calls.append((rid, state))

    register_resumer({S.EVALUATING}, resume)
    try:
        await walk(db, request_id, [S.ANALYZING, S.RESEARCHING, S.EVALUATING])
        report = await recover_interrupted(SessionLocal)
        assert (request_id, S.EVALUATING) in calls and request_id in report.resumed
        assert await status_of(request_id) == S.EVALUATING
    finally:
        clear_resumers()


async def test_recovery_ignores_idle_and_terminal_requests(db, request_id):
    clear_resumers()
    report = await recover_interrupted(SessionLocal)  # request_id is CREATED → not touched
    assert request_id not in report.failed + report.resumed
    assert S.CREATED not in AGENT_ACTIVE and S.AWAITING_APPROVAL not in AGENT_ACTIVE
