"""Intent Agent: natural-language request → validated ProcurementRequirement (or clarification questions)."""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.agents.core.base import AgentContext, BaseAgent
from app.agents.outcome import StepOutcome
from app.agents.schemas import ProcurementRequirement, RequirementItem

CATEGORIES = ("laptops", "monitors", "peripherals", "furniture", "software")


class ExtractedItem(BaseModel):
    product_name: str | None = Field(None, description="What to buy, e.g. 'laptop'")
    quantity: int | None = Field(None, description="Number of units; null if not stated")
    specifications: dict[str, Any] = Field(default_factory=dict, description="Concrete constraints, e.g. {'ram_gb_min': 16}")
    category: Literal["laptops", "monitors", "peripherals", "furniture", "software"] | None = None


class Extraction(BaseModel):
    """What the LLM returns. Deliberately lenient — Python validates it afterwards."""

    title: str | None = Field(None, description="Short title, max 8 words")
    items: list[ExtractedItem] = Field(default_factory=list)
    budget: Decimal | None = Field(None, description="Maximum total spend; null if not stated")
    currency: str | None = Field(None, description="ISO code such as USD; null if not stated")
    deadline: date | None = Field(None, description="Delivery deadline as YYYY-MM-DD; null if not stated")
    clarification_questions: list[str] = Field(default_factory=list)


class IntentResult(StepOutcome):
    title: str | None = None
    requirement: ProcurementRequirement | None = None
    problems: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    llm_provider: str | None = None


SYSTEM_PROMPT = f"""You are the Intent Agent of Procura AI, an enterprise procurement system.
Turn a purchase request written in plain language into structured requirements.

Rules:
- Extract only what the request states or clearly implies. Never invent quantities, budgets or deadlines — use null.
- One entry in `items` per distinct product. `product_name` is a short generic noun phrase ("laptop", "office chair").
- `specifications` holds concrete technical constraints as key/value pairs, using _min/_max suffixes for limits
  (e.g. {{"ram_gb_min": 16, "screen_in_min": 14}}).
- `category` is one of: {", ".join(CATEGORIES)} — or null if none fits.
- Resolve relative dates ("by Dec 1", "next Friday") against today's date given in the prompt.
- `budget` is the maximum total spend for the whole request, as a number.
- If the product, the quantity or the budget is missing or ambiguous, add one short clarification question for each."""


class IntentAgent(BaseAgent):
    name = "intent"
    description = "Extracts structured procurement requirements from natural language."
    system_prompt = SYSTEM_PROMPT
    timeout_s = 240.0
    max_tool_calls = 5

    async def run(self, ctx: AgentContext) -> IntentResult:
        req = ctx.input["request"]
        today = date.fromisoformat(ctx.input.get("today") or date.today().isoformat())
        form = {k: req.get(k) for k in ("budget", "currency", "deadline") if req.get(k)}
        prompt = (
            f"Today is {today.isoformat()}.\n"
            f"Request title: {req.get('title') or '(none)'}\n"
            f"Request text: {req['raw_text']}\n"
            f"Form fields entered by the requester (authoritative when present): {form or 'none'}"
        )
        gen = await ctx.generate(Extraction, prompt, purpose="intent.extract")
        ex = gen.data

        problems: list[str] = []
        questions: list[str] = []

        def ask(problem: str, question: str) -> None:
            problems.append(problem)
            questions.append(question)

        # Form fields beat the model's reading of the text.
        budget = Decimal(str(req["budget"])) if req.get("budget") else ex.budget
        currency = (req.get("currency") or ex.currency or "USD").upper()
        deadline = date.fromisoformat(req["deadline"]) if req.get("deadline") else ex.deadline

        if not ex.items:
            ask("no product identified", "Which product or service do you need?")
        items: list[RequirementItem] = []
        for i, it in enumerate(ex.items, 1):
            label = it.product_name or f"item {i}"
            if not it.product_name or len(it.product_name.strip()) < 2:
                ask(f"item {i}: product not specified", f"What exactly is item {i}?")
            if it.quantity is None:
                ask(f"{label}: quantity missing", f"How many {label} units do you need?")
            elif it.quantity < 1:
                ask(f"{label}: invalid quantity {it.quantity}", f"How many {label} units do you need? ({it.quantity} is not a valid quantity)")
            if it.product_name and it.quantity and it.quantity >= 1:
                items.append(RequirementItem(product_name=it.product_name.strip(), quantity=it.quantity,
                                             specifications=it.specifications, category=it.category))

        requirement = None
        if items and not problems:
            try:
                requirement = ProcurementRequirement(items=items, budget=budget, currency=currency, deadline=deadline)
            except ValidationError as e:
                ask("requirements invalid", f"Please check the request details: {e.errors()[0]['msg']}")
        if requirement is not None:
            check = await ctx.tool("validate_requirements", requirement)
            for field in check.missing_fields:
                if field == "budget":
                    ask("budget missing", "What is the maximum budget for this purchase?")
                else:
                    ask(f"{field} missing", f"Please provide the {field}.")
            for issue in check.issues:
                if "deadline" in issue:
                    ask(issue, f"The deadline {deadline} has already passed — what is the new delivery date?")
                elif "currency" in issue:
                    ask(issue, "Which currency should we pay in (USD, EUR or GBP)?")
                else:
                    ask(issue, f"Please clarify: {issue}")

        if problems:
            # Keep the model's own questions only when Python also found a real gap.
            for q in ex.clarification_questions:
                if q not in questions:
                    questions.append(q)
            return IntentResult(outcome="clarify", reason="Needs clarification: " + "; ".join(problems), title=ex.title,
                                requirement=None, problems=problems, clarification_questions=questions, llm_provider=gen.provider)

        ctx.notes.append(f"{len(requirement.items)} item(s) extracted via {gen.provider}")
        return IntentResult(title=ex.title, requirement=requirement, llm_provider=gen.provider)
