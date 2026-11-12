"""Procurement Agent: turns the recommended offer set into a saved DRAFT purchase order.

Deterministic by design — no LLM. Prices, totals and PO numbers are computed in Python from
database records, so the model can never influence what is ordered or what it costs.
"""

import hashlib
from decimal import Decimal

from pydantic import BaseModel, Field

from app.agents.core.base import AgentContext, BaseAgent
from app.agents.evaluation import Candidate
from app.agents.outcome import StepOutcome
from app.core.config import get_settings
from app.tools.procurement import PurchaseOrderView


class ProcurementResult(StepOutcome):
    purchase_order: PurchaseOrderView | None = None
    vendor_name: str | None = None
    summary: str | None = None
    price_changed: bool = False
    unavailable: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the Procurement Agent of Procura AI. You convert an approved vendor recommendation
into a purchase order. All quantities, prices and totals are computed by deterministic tools from the
database; you never estimate or alter money amounts."""


class ProcurementAgent(BaseAgent):
    name = "procurement"
    description = "Validates the selection and creates the purchase order."
    system_prompt = SYSTEM_PROMPT
    timeout_s = 60.0
    max_tool_calls = 30

    async def run(self, ctx: AgentContext) -> ProcurementResult:
        choice = Candidate.model_validate(ctx.input["recommended"])
        tax_rate = Decimal(str(get_settings().tax_rate))

        # Re-check availability right before ordering — stock may have moved since research.
        unavailable = []
        for line in choice.lines:
            a = await ctx.tool("check_availability", {"offer_id": str(line.offer_id), "quantity": line.quantity})
            if not a.available:
                unavailable.append(f"{line.sku}: {', '.join(a.issues)}")
        if unavailable:
            return ProcurementResult(outcome="reject", unavailable=unavailable, vendor_name=choice.vendor_name,
                                     reason=f"Selected offers are no longer available: {'; '.join(unavailable)}")

        lines = [{"offer_id": str(ln.offer_id), "quantity": ln.quantity} for ln in choice.lines]
        fingerprint = hashlib.sha256(repr(sorted((ln["offer_id"], ln["quantity"]) for ln in lines)).encode()).hexdigest()[:16]
        po = await ctx.tool(
            "create_purchase_order",
            {"lines": lines, "tax_rate": str(tax_rate), "notes": f"Recommended by evaluation (score {choice.score}/100)"},
            # Same request + same lines → same PO, even if the workflow is re-run or resumed after a crash.
            idempotency_key=ctx.idempotency_key(f"create_po:{fingerprint}"),
        )

        changed = po.total != choice.total
        if changed:
            ctx.notes.append(f"price changed since evaluation: {choice.total} → {po.total}")
        n = len(po.lines or [])
        summary = (f"{po.po_number}: {n} line{'s' if n != 1 else ''} from {choice.vendor_name}. "
                   f"Subtotal {po.subtotal}, tax {po.tax_amount}, total {po.total} {po.currency}. "
                   f"Expected delivery in {choice.lead_time_days} days ({choice.delivery_date}).")
        return ProcurementResult(purchase_order=po, vendor_name=choice.vendor_name, summary=summary, price_changed=changed)
