"""Finance Agent: validates the drafted PO and either sends it for human approval or blocks it.

Deterministic by design — no LLM. Every check is a Decimal comparison against database records.
"""

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from app.agents.core.base import AgentContext, BaseAgent
from app.agents.outcome import StepOutcome
from app.models.enums import PurchaseOrderStatus
from app.tools.common import money


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class FinanceReport(BaseModel):
    purchase_order_id: uuid.UUID
    po_number: str
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    budget: Decimal | None
    remaining_budget: Decimal | None
    approval_threshold: Decimal
    requires_senior_approval: bool
    checks: list[Check]


class FinanceResult(StepOutcome):
    report: FinanceReport | None = None
    blocked_reasons: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the Finance Agent of Procura AI. You validate purchase orders against budget,
currency and arithmetic rules before they reach a human approver. Financial decisions are made only by
deterministic checks on database records; you never approve payments yourself."""


class FinanceAgent(BaseAgent):
    name = "finance"
    description = "Validates budget, currency and totals; routes the PO to human approval or blocks it."
    system_prompt = SYSTEM_PROMPT
    timeout_s = 60.0
    max_tool_calls = 10

    async def run(self, ctx: AgentContext) -> FinanceResult:
        po_id = ctx.input["purchase_order_id"]
        po = await ctx.tool("get_purchase_order", {"purchase_order_id": po_id})
        cur = await ctx.tool("validate_currency", {"currency": po.currency})
        budget = await ctx.tool("validate_budget", {"amount": str(po.total), "currency": po.currency})

        lines_sum = money(sum((ln.line_total for ln in po.lines or []), Decimal("0")))
        lines_ok = all(ln.line_total == money(ln.unit_price * ln.quantity) for ln in po.lines or [])
        checks = [
            # PENDING_APPROVAL is accepted so a run resumed after a crash (status already moved) still passes.
            Check(name="status", passed=po.status in (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.PENDING_APPROVAL), detail=f"PO status is {po.status}"),
            Check(name="has_lines", passed=bool(po.lines), detail=f"{len(po.lines or [])} line(s)"),
            Check(name="line_totals", passed=lines_ok, detail="each line total = unit price × quantity"),
            Check(name="subtotal", passed=lines_sum == po.subtotal, detail=f"sum of lines {lines_sum} vs subtotal {po.subtotal}"),
            Check(name="total", passed=po.subtotal + po.tax_amount == po.total, detail=f"{po.subtotal} + {po.tax_amount} = {po.total}"),
            Check(name="currency_supported", passed=cur.supported, detail=f"{po.currency} in {cur.supported_currencies}"),
            Check(name="currency_matches_request", passed=cur.matches_request, detail=f"PO currency {po.currency}"),
            Check(name="budget", passed=budget.within_budget, detail="; ".join(budget.issues) or f"{po.total} within budget {budget.budget}"),
        ]
        report = FinanceReport(
            purchase_order_id=po.purchase_order_id, po_number=po.po_number, currency=po.currency, subtotal=po.subtotal,
            tax_amount=po.tax_amount, total=po.total, budget=budget.budget, remaining_budget=budget.remaining,
            approval_threshold=budget.approval_threshold, requires_senior_approval=po.total > budget.approval_threshold, checks=checks,
        )
        failed = [f"{c.name}: {c.detail}" for c in checks if not c.passed]

        if failed:
            await ctx.tool("update_purchase_order",
                           {"purchase_order_id": po_id, "status": PurchaseOrderStatus.CANCELLED, "notes": "Blocked by finance: " + "; ".join(failed)},
                           idempotency_key=ctx.idempotency_key(f"po_status:{po_id}:CANCELLED"))
            return FinanceResult(outcome="reject", report=report, blocked_reasons=failed, reason="Finance blocked the purchase: " + "; ".join(failed))

        await ctx.tool("update_purchase_order", {"purchase_order_id": po_id, "status": PurchaseOrderStatus.PENDING_APPROVAL},
                       idempotency_key=ctx.idempotency_key(f"po_status:{po_id}:PENDING_APPROVAL"))
        if report.requires_senior_approval:
            ctx.notes.append(f"total {po.total} is above the {budget.approval_threshold} threshold — senior approval advised")
        return FinanceResult(report=report)
