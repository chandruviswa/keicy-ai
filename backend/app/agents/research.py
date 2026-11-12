"""Research Agent: finds catalogue products that meet each requirement and retrieves real vendor quotes.

The LLM only judges which *catalogue candidates* meet the specs. Any SKU it names that the
catalogue search didn't return is discarded and recorded — offers always come from the DB.
"""

import uuid

from pydantic import BaseModel, Field

from app.agents.core.base import AgentContext, BaseAgent
from app.agents.outcome import StepOutcome
from app.agents.schemas import ProcurementRequirement
from app.tools.catalog import OfferQuote

MAX_PRODUCTS_PER_ITEM = 3


class SkuJudgement(BaseModel):
    sku: str
    meets_requirements: bool
    reason: str = Field(description="One sentence: which specs are met or missed")


class MatchJudgement(BaseModel):
    matches: list[SkuJudgement]


class MatchedProduct(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    specs: dict
    reason: str


class ItemResearch(BaseModel):
    index: int
    product_name: str
    quantity: int
    specifications: dict
    products: list[MatchedProduct]
    quotes: list[OfferQuote]
    eligible_quotes: int
    rejected_candidates: list[str] = Field(default_factory=list)  # "SKU: reason"
    discarded_unknown_skus: list[str] = Field(default_factory=list)  # model-invented SKUs


class ResearchResult(StepOutcome):
    items: list[ItemResearch] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the Research Agent of Procura AI.
You receive one requirement and a list of catalogue products. Decide, for EACH listed product, whether it
meets the requirement.

Rules:
- Judge only the products listed. Never mention or invent other products or SKUs.
- A product meets a `_min` constraint if its spec is greater than or equal to it, and a `_max` constraint if lower or equal.
- If the requirement names no concrete specs, any product of the right kind meets it.
- A product of a clearly different kind (e.g. a monitor when a laptop is required) does not meet it.
- Give a one-sentence reason per product."""


class ResearchAgent(BaseAgent):
    name = "research"
    description = "Matches requirements to catalogue products and gathers vendor quotes."
    system_prompt = SYSTEM_PROMPT
    timeout_s = 300.0
    max_tool_calls = 60

    async def run(self, ctx: AgentContext) -> ResearchResult:
        req = ProcurementRequirement.model_validate(ctx.input["requirement"])
        results: list[ItemResearch] = []

        for index, item in enumerate(req.items):
            found = await ctx.tool("search_catalog", {"query": item.product_name, "category": item.category, "limit": 10})
            candidates = found.products
            if not candidates and item.category:
                candidates = (await ctx.tool("search_catalog", {"query": item.product_name, "limit": 10})).products
            if not candidates and item.category:
                candidates = (await ctx.tool("search_catalog", {"category": item.category, "limit": 10})).products
            if not candidates:
                return ResearchResult(outcome="reject", items=results,
                                      reason=f"No product in the catalogue matches '{item.product_name}'")

            listing = [{"sku": p.sku, "name": p.name, "category": p.category, "description": p.description, "specs": p.specs} for p in candidates]
            gen = await ctx.generate(
                MatchJudgement,
                f"Requirement: {item.quantity} × {item.product_name}; specifications: {item.specifications or 'none'}\n"
                f"Catalogue products:\n{listing}",
                purpose="research.match",
            )
            by_sku = {p.sku: p for p in candidates}
            judged = {m.sku: m for m in gen.data.matches}
            unknown = [s for s in judged if s not in by_sku]
            accepted = [by_sku[s] for s, m in judged.items() if m.meets_requirements and s in by_sku][:MAX_PRODUCTS_PER_ITEM]
            rejected = [f"{s}: {m.reason}" for s, m in judged.items() if not m.meets_requirements and s in by_sku]
            if unknown:
                ctx.notes.append(f"discarded SKUs not in catalogue: {unknown}")

            if not accepted:
                why = "; ".join(rejected) or "no candidate was judged suitable"
                return ResearchResult(outcome="reject", items=results,
                                      reason=f"No catalogue product meets the specifications for {item.product_name}: {why}")

            quotes: list[OfferQuote] = []
            for p in accepted:
                q = await ctx.tool("get_vendor_quotes", {"product_id": str(p.product_id), "quantity": item.quantity})
                quotes.extend(q.quotes)
            eligible = sum(q.eligible for q in quotes)
            results.append(ItemResearch(
                index=index, product_name=item.product_name, quantity=item.quantity, specifications=item.specifications,
                products=[MatchedProduct(product_id=p.product_id, sku=p.sku, name=p.name, specs=p.specs, reason=judged[p.sku].reason) for p in accepted],
                quotes=quotes, eligible_quotes=eligible, rejected_candidates=rejected, discarded_unknown_skus=unknown,
            ))
            if eligible == 0:
                issues = sorted({f"{q.vendor_name}: {', '.join(q.issues)}" for q in quotes})
                return ResearchResult(outcome="reject", items=results,
                                      reason=f"No vendor can currently supply {item.quantity} × {item.product_name} ({'; '.join(issues)})")

        ctx.notes.append(f"{sum(r.eligible_quotes for r in results)} eligible quote(s) across {len(results)} item(s)")
        return ResearchResult(items=results)
