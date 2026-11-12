"""Evaluation Agent: ranks vendors with deterministic Python scoring; the LLM only writes the explanation.

A candidate is one vendor supplying every item (one PO = one vendor). For each item the vendor's
cheapest eligible matching offer is used. Totals come from calculate_purchase_total (DB prices).

Hard rules: every line must be available, and delivery must meet the deadline (if one was given).
Budget is a ranking factor here; Finance enforces it as a hard rule.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field

from app.agents.core.base import AgentContext, BaseAgent
from app.agents.outcome import StepOutcome
from app.agents.research import ResearchResult
from app.agents.schemas import ProcurementRequirement
from app.ai.errors import LLMGatewayError
from app.core.config import get_settings

WEIGHTS = {"price": Decimal("0.45"), "speed": Decimal("0.25"), "rating": Decimal("0.15"), "budget": Decimal("0.15")}


class CandidateLine(BaseModel):
    item_index: int
    offer_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    lead_time_days: int


class Candidate(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_rating: Decimal
    lines: list[CandidateLine]
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    lead_time_days: int  # slowest line
    delivery_date: date
    meets_deadline: bool
    within_budget: bool
    scores: dict[str, Decimal]
    score: Decimal  # 0–100
    rank: int = 0


class Explanation(BaseModel):
    summary: str = Field(description="2–3 sentences explaining why the recommended vendor wins")
    reasons: list[str] = Field(description="Short bullet points comparing the options")
    risks: list[str] = Field(default_factory=list, description="Concerns the approver should know about")


class EvaluationResult(StepOutcome):
    ranking: list[Candidate] = Field(default_factory=list)
    recommended: Candidate | None = None
    excluded: list[str] = Field(default_factory=list)  # vendors that couldn't be considered, with why
    explanation: Explanation | None = None
    explanation_source: str | None = None  # "llm:<provider>" or "rules"
    budget_check: dict | None = None


SYSTEM_PROMPT = """You are the Evaluation Agent of Keicy AI.
The vendor ranking has already been computed by deterministic rules. Your job is to explain it to a
human approver — clearly, briefly and only using the numbers given.

Rules:
- The recommended vendor is fixed. Do not recommend a different one.
- Quote prices, totals, lead times and ratings exactly as given; do not compute new numbers.
- Mention trade-offs (e.g. cheaper but slower) and any risk such as being over budget or a tight deadline."""


def _ratio(best: Decimal, value: Decimal) -> Decimal:
    return Decimal("1") if value <= 0 else min(Decimal("1"), best / value)


class EvaluationAgent(BaseAgent):
    name = "evaluation"
    description = "Scores complete vendor offers and explains the recommendation."
    system_prompt = SYSTEM_PROMPT
    timeout_s = 240.0
    max_tool_calls = 40

    async def run(self, ctx: AgentContext) -> EvaluationResult:
        req = ProcurementRequirement.model_validate(ctx.input["requirement"])
        research = ResearchResult.model_validate(ctx.input["research"])
        today = date.fromisoformat(ctx.input.get("today") or date.today().isoformat())
        tax_rate = Decimal(str(get_settings().tax_rate))

        # vendor → item index → that vendor's eligible quotes for the item
        by_vendor: dict[uuid.UUID, dict[int, list]] = {}
        names: dict[uuid.UUID, tuple[str, Decimal]] = {}
        for item in research.items:
            for q in item.quotes:
                names[q.vendor_id] = (q.vendor_name, q.vendor_rating)
                if q.eligible:
                    by_vendor.setdefault(q.vendor_id, {}).setdefault(item.index, []).append(q)

        # Trace a side-by-side comparison per item (also re-validates the offers against the DB).
        for item in research.items:
            ids = [str(q.offer_id) for q in item.quotes if q.eligible]
            if len(ids) > 1:
                await ctx.tool("compare_offers", {"offer_ids": ids, "quantity": item.quantity})

        excluded: list[str] = []
        raw: list[dict] = []
        needed = {i.index for i in research.items}
        for vendor_id, per_item in by_vendor.items():
            missing = needed - set(per_item)
            if missing:
                excluded.append(f"{names[vendor_id][0]}: cannot supply {', '.join(research.items[i].product_name for i in sorted(missing))}")
                continue
            picks = {i: min(qs, key=lambda q: (q.line_total, q.lead_time_days)) for i, qs in per_item.items()}
            priced = await ctx.tool("calculate_purchase_total", {
                "lines": [{"offer_id": str(q.offer_id), "quantity": q.quantity} for q in picks.values()],
                "tax_rate": str(tax_rate),
            })
            if not priced.all_lines_available:
                excluded.append(f"{names[vendor_id][0]}: availability changed during evaluation")
                continue
            lead = max(q.lead_time_days for q in picks.values())
            delivery = today + timedelta(days=lead)
            raw.append({
                "vendor_id": vendor_id, "picks": picks, "priced": priced, "lead": lead, "delivery": delivery,
                "meets_deadline": req.deadline is None or delivery <= req.deadline,
                "within_budget": req.budget is None or priced.total <= req.budget,
            })

        if not raw:
            return EvaluationResult(outcome="reject", excluded=excluded,
                                    reason="No single vendor can supply every item" + (f" ({'; '.join(excluded)})" if excluded else ""))

        late = [r for r in raw if not r["meets_deadline"]]
        raw = [r for r in raw if r["meets_deadline"]]
        for r in late:
            excluded.append(f"{names[r['vendor_id']][0]}: earliest delivery {r['delivery']} is after the {req.deadline} deadline")
        if not raw:
            fastest = min(late, key=lambda r: r["delivery"])
            return EvaluationResult(outcome="reject", excluded=excluded,
                                    reason=f"No vendor can deliver by {req.deadline}; the earliest is {fastest['delivery']} from {names[fastest['vendor_id']][0]}")

        best_total = min(r["priced"].total for r in raw)
        best_lead = min(r["lead"] for r in raw)
        candidates = []
        for r in raw:
            name, rating = names[r["vendor_id"]]
            scores = {
                "price": _ratio(best_total, r["priced"].total),
                "speed": _ratio(Decimal(best_lead + 1), Decimal(r["lead"] + 1)),
                "rating": rating / Decimal(5),
                "budget": Decimal(1) if r["within_budget"] else Decimal(0),
            }
            score = sum(WEIGHTS[k] * v for k, v in scores.items()) * 100
            candidates.append(Candidate(
                vendor_id=r["vendor_id"], vendor_name=name, vendor_rating=rating, currency=r["priced"].currency,
                lines=[CandidateLine(item_index=i, offer_id=q.offer_id, product_id=q.product_id, sku=q.sku, quantity=q.quantity,
                                     unit_price=q.unit_price, line_total=q.line_total, lead_time_days=q.lead_time_days)
                       for i, q in sorted(r["picks"].items())],
                subtotal=r["priced"].subtotal, tax_amount=r["priced"].tax_amount, total=r["priced"].total,
                lead_time_days=r["lead"], delivery_date=r["delivery"], meets_deadline=True, within_budget=r["within_budget"],
                scores={k: v.quantize(Decimal("0.001")) for k, v in scores.items()}, score=score.quantize(Decimal("0.1")),
            ))
        # Deterministic tie-breaks: score, then cheaper, faster, better rated, name.
        candidates.sort(key=lambda c: (-c.score, c.total, c.lead_time_days, -c.vendor_rating, c.vendor_name))
        for rank, c in enumerate(candidates, 1):
            c.rank = rank
        winner = candidates[0]

        budget_check = await ctx.tool("validate_budget", {"amount": str(winner.total), "currency": winner.currency})
        explanation, source = await self._explain(ctx, req, candidates, excluded)
        return EvaluationResult(ranking=candidates, recommended=winner, excluded=excluded, explanation=explanation,
                                explanation_source=source, budget_check=budget_check.model_dump(mode="json"))

    async def _explain(self, ctx: AgentContext, req: ProcurementRequirement, ranking: list[Candidate], excluded: list[str]):
        table = [
            {"rank": c.rank, "vendor": c.vendor_name, "total": str(c.total), "currency": c.currency, "lead_time_days": c.lead_time_days,
             "delivery_date": c.delivery_date.isoformat(), "rating": str(c.vendor_rating), "within_budget": c.within_budget,
             "score": str(c.score), "products": [ln.sku for ln in c.lines]}
            for c in ranking[:5]
        ]
        prompt = (f"Requirement: {req.model_dump(mode='json')}\nBudget: {req.budget} {req.currency}\n"
                  f"Ranking (rank 1 is recommended): {table}\nExcluded: {excluded or 'none'}")
        try:
            gen = await ctx.generate(Explanation, prompt, purpose="evaluation.explain", max_tokens=800)
            return gen.data, f"llm:{gen.provider}"
        except LLMGatewayError:
            # The choice never depended on the model, so a rules-based explanation is a safe fallback.
            w = ranking[0]
            reasons = [f"{c.vendor_name}: {c.total} {c.currency}, {c.lead_time_days} days, rating {c.vendor_rating}" for c in ranking[:3]]
            risks = [] if w.within_budget else [f"Total {w.total} exceeds the budget of {req.budget}"]
            return Explanation(summary=f"{w.vendor_name} has the best overall score ({w.score}/100) on price, delivery time, rating and budget fit.",
                               reasons=reasons, risks=risks), "rules"
