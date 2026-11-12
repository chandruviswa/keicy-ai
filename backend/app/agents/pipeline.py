"""The procurement pipeline: drives the five agents through the workflow states.

  ANALYZING ─intent→ RESEARCHING ─research→ EVALUATING ─evaluation+procurement→ ORDER_DRAFTED
            → FINANCE_VALIDATING ─finance→ AWAITING_APPROVAL   (then a human decides)

Each agent returns an outcome: continue / reject (→ REJECTED) / clarify (→ FAILED with questions).
A crashed or timed-out agent moves the request to FAILED. The pipeline can start from any of these
states, reloading earlier agents' outputs from agent_runs — that is how restart recovery resumes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.core.base import AgentResult, BaseAgent
from app.agents.core.runner import AgentRunner
from app.agents.evaluation import EvaluationAgent, EvaluationResult
from app.agents.finance import FinanceAgent, FinanceResult
from app.agents.intent import IntentAgent, IntentResult
from app.agents.outcome import StepOutcome
from app.agents.procurement import ProcurementAgent, ProcurementResult
from app.agents.reconciliation import ReconciliationAgent, ReconciliationResult
from app.agents.research import ResearchAgent, ResearchResult
from app.models import AgentRun, Payment, ProcurementRequest
from app.models.enums import RunStatus, WorkflowState as S
from app.workflow import Actor, WorkflowError, transition
from app.workflow.events import emit
from app.workflow.recovery import register_resumer

log = logging.getLogger("keicy.pipeline")

RESUMABLE = frozenset({S.ANALYZING, S.RESEARCHING, S.EVALUATING, S.ORDER_DRAFTED, S.FINANCE_VALIDATING, S.PAYMENT_COMPLETED, S.RECONCILING})


class Stop(Exception):
    """Pipeline ended early (rejected, failed, cancelled). Not an error."""


class ProcurementPipeline:
    def __init__(self, runner: AgentRunner | None = None, sessions: async_sessionmaker[AsyncSession] | None = None) -> None:
        if sessions is None:
            from app.db.session import SessionLocal

            sessions = SessionLocal
        self.sessions = sessions
        self.runner = runner or AgentRunner(sessions=sessions)

    async def run(self, request_id: uuid.UUID, start: S = S.ANALYZING) -> S:
        """Run from `start` until AWAITING_APPROVAL or an exit state. Returns the final state."""
        try:
            await self._run(request_id, S(start))
        except Stop:
            pass
        return await self._status(request_id)

    async def _run(self, rid: uuid.UUID, state: S) -> None:
        req = await self._request_input(rid)
        today = date.today().isoformat()
        prev = await self._previous_outputs(rid)

        if state in (S.PAYMENT_COMPLETED, S.RECONCILING):
            await self._reconcile(rid, state)
            return

        if state == S.ANALYZING:
            intent = await self._step(rid, S.ANALYZING, IntentAgent(), {"request": req, "today": today}, IntentResult)
            await self._save_requirements(rid, intent)
            prev["intent"] = intent.model_dump(mode="json")
            state = await self._advance(rid, S.RESEARCHING, "intent")

        requirement = (prev.get("intent") or {}).get("requirement")

        if state == S.RESEARCHING:
            research = await self._step(rid, S.RESEARCHING, ResearchAgent(), {"requirement": requirement}, ResearchResult)
            prev["research"] = research.model_dump(mode="json")
            state = await self._advance(rid, S.EVALUATING, "research")

        if state == S.EVALUATING:
            evaluation = await self._step(rid, S.EVALUATING, EvaluationAgent(),
                                          {"requirement": requirement, "research": prev["research"], "today": today}, EvaluationResult)
            procurement = await self._step(rid, S.EVALUATING, ProcurementAgent(),
                                           {"recommended": evaluation.recommended.model_dump(mode="json")}, ProcurementResult)
            prev["procurement"] = procurement.model_dump(mode="json")
            state = await self._advance(rid, S.ORDER_DRAFTED, "procurement",
                                        payload={"purchase_order_id": str(procurement.purchase_order.purchase_order_id),
                                                 "po_number": procurement.purchase_order.po_number})

        if state == S.ORDER_DRAFTED:
            state = await self._advance(rid, S.FINANCE_VALIDATING, "finance")

        if state == S.FINANCE_VALIDATING:
            po = prev["procurement"]["purchase_order"]
            finance = await self._step(rid, S.FINANCE_VALIDATING, FinanceAgent(), {"purchase_order_id": po["purchase_order_id"]}, FinanceResult)
            await self._advance(rid, S.AWAITING_APPROVAL, "finance", approval_required={
                "purchase_order_id": po["purchase_order_id"], "po_number": po["po_number"], "total": str(finance.report.total),
                "currency": finance.report.currency, "requires_senior_approval": finance.report.requires_senior_approval,
            })

    async def _reconcile(self, rid: uuid.UUID, state: S) -> None:
        """After a verified capture: PAYMENT_COMPLETED → RECONCILING → COMPLETED (or FAILED on a discrepancy)."""
        if state == S.PAYMENT_COMPLETED:
            await self._advance(rid, S.RECONCILING, "reconciliation")
        # A discrepancy can't be "rejected" (money has moved) — it goes to FAILED for finance to review.
        result = await self._step(rid, S.RECONCILING, ReconciliationAgent(), {}, ReconciliationResult, reject_to=S.FAILED)
        async with self.sessions() as db:
            payment = await db.get(Payment, result.payment_id)
            payment.receipt = result.receipt.model_dump(mode="json")
            payment.reconciled_at = datetime.now(timezone.utc)
            await transition(db, rid, S.COMPLETED, actor=Actor.agent("reconciliation"), reason=f"Reconciled; receipt {result.receipt.receipt_number}",
                             payload={"receipt_number": result.receipt.receipt_number, "capture_id": result.receipt.capture_id}, commit=False)
            await db.commit()

    # ---------- steps ----------

    async def _step(self, rid: uuid.UUID, expected: S, agent: BaseAgent, input: dict[str, Any], schema: type[StepOutcome],
                    reject_to: S = S.REJECTED):
        if await self._status(rid) != expected:  # cancelled (or moved) while the previous agent was working
            raise Stop()
        res: AgentResult = await self.runner.run(agent, rid, input)
        if not res.ok:
            await self._end(rid, S.FAILED, agent.name, f"{agent.name} agent {res.status.lower()}: {res.error}")
        parsed = schema.model_validate(res.output)
        if parsed.outcome == "reject":
            await self._end(rid, reject_to, agent.name, parsed.reason or f"Rejected by {agent.name} agent")
        if parsed.outcome == "clarify":
            await self._end(rid, S.FAILED, agent.name, parsed.reason or "Needs clarification",
                            payload={"clarification_questions": getattr(parsed, "clarification_questions", [])})
        return parsed

    async def _advance(self, rid: uuid.UUID, to: S, agent: str, payload: dict | None = None, approval_required: dict | None = None) -> S:
        try:
            async with self.sessions() as db:
                await transition(db, rid, to, actor=Actor.agent(agent), payload=payload, commit=False)
                if approval_required:
                    await emit(db, rid, "approval.required", approval_required)
                await db.commit()
        except WorkflowError as e:  # e.g. cancelled meanwhile — stop quietly
            log.info("Pipeline for %s stopped: %s", rid, e)
            raise Stop() from e
        return to

    async def _end(self, rid: uuid.UUID, to: S, agent: str, reason: str, payload: dict | None = None) -> None:
        try:
            async with self.sessions() as db:
                await transition(db, rid, to, actor=Actor.agent(agent), reason=reason[:2000], payload=payload)
        except WorkflowError as e:
            log.info("Could not move %s to %s: %s", rid, to, e)
        raise Stop()

    # ---------- data ----------

    async def _status(self, rid: uuid.UUID) -> S:
        async with self.sessions() as db:
            return S(await db.scalar(select(ProcurementRequest.status).where(ProcurementRequest.id == rid)))

    async def _request_input(self, rid: uuid.UUID) -> dict[str, Any]:
        async with self.sessions() as db:
            pr = await db.get(ProcurementRequest, rid)
            return {"id": str(pr.id), "raw_text": pr.raw_text, "title": pr.title, "currency": pr.currency,
                    "budget": str(pr.budget) if pr.budget is not None else None,
                    "deadline": pr.deadline.isoformat() if pr.deadline else None}

    async def _previous_outputs(self, rid: uuid.UUID) -> dict[str, dict]:
        """Latest successful output per agent — lets a resumed pipeline skip finished steps."""
        async with self.sessions() as db:
            runs = (await db.scalars(select(AgentRun).where(AgentRun.request_id == rid, AgentRun.status == RunStatus.SUCCEEDED)
                                     .order_by(AgentRun.created_at))).all()
        return {r.agent_name: r.output for r in runs}

    async def _save_requirements(self, rid: uuid.UUID, intent: IntentResult) -> None:
        """Store the validated requirements, and copy budget / currency / deadline read from the text onto
        the request — Finance checks the request's own budget, not the model's output."""
        req = intent.requirement
        async with self.sessions() as db:
            pr = await db.get(ProcurementRequest, rid)
            pr.requirements = req.model_dump(mode="json")
            if not pr.title:
                first = req.items[0]
                fallback = f"{first.quantity} × {first.product_name}" + (f" (+{len(req.items) - 1} more)" if len(req.items) > 1 else "")
                pr.title = (intent.title or fallback)[:200]
            if pr.budget is None and req.budget is not None:
                pr.budget = req.budget
            if pr.deadline is None and req.deadline is not None:
                pr.deadline = req.deadline
            pr.currency = req.currency
            await db.commit()


# ---------- background execution ----------


class WorkflowExecutor:
    """Runs pipelines as background tasks (one per request) and remembers them so tests/APIs can await them."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self.pipeline_factory = ProcurementPipeline

    def is_running(self, rid: uuid.UUID) -> bool:
        t = self._tasks.get(rid)
        return t is not None and not t.done()

    def start(self, rid: uuid.UUID, start: S = S.ANALYZING) -> asyncio.Task:
        if self.is_running(rid):
            raise WorkflowError(f"Workflow for {rid} is already running")
        task = asyncio.create_task(self._run(rid, start), name=f"pipeline-{rid}")
        self._tasks[rid] = task
        task.add_done_callback(lambda _t, rid=rid: self._tasks.pop(rid, None) if self._tasks.get(rid) is _t else None)
        return task

    async def wait(self, rid: uuid.UUID) -> None:
        t = self._tasks.get(rid)
        if t:
            await asyncio.shield(t)

    async def _run(self, rid: uuid.UUID, start: S) -> None:
        pipeline = self.pipeline_factory()
        try:
            await pipeline.run(rid, start)
        except Exception as e:  # noqa: BLE001 — never leave a request stuck in an active state
            log.exception("Pipeline crashed for %s", rid)
            try:
                async with pipeline.sessions() as db:
                    await transition(db, rid, S.FAILED, actor=Actor.system("pipeline"), reason=f"Pipeline error: {type(e).__name__}: {e}"[:2000])
            except WorkflowError:
                pass


executor = WorkflowExecutor()


def register_pipeline_resumers() -> None:
    async def resume(rid: uuid.UUID, state: S) -> None:
        executor.start(rid, state)

    register_resumer(RESUMABLE, resume)

    async def resolve_payment(rid: uuid.UUID, state: S) -> None:
        # Interrupted mid-capture: the outcome is unknown until PayPal tells us.
        from app.db.session import SessionLocal
        from app.payments.service import resolve_processing

        await resolve_processing(SessionLocal, rid)

    register_resumer({S.PAYMENT_PROCESSING}, resolve_payment)
