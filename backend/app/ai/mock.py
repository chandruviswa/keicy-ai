"""Deterministic rule-based stand-in for an LLM (LLM_PROVIDERS=mock). Development, demos and E2E tests only.

It answers the three agents that use a model by reading the same prompt a real model would get:
  * Intent: regexes for quantity, product, "16GB", "$15,000", "by Dec 1";
  * Research: compares each catalogue product's specs with the requirement's _min/_max constraints;
  * Evaluation: describes rank 1 of the ranking it is given.
Everything downstream (tools, scoring, money) is the real code. Refused when APP_ENV=production.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import date

from app.ai.errors import LLMError, LLMErrorKind
from app.ai.providers import LLMProvider, ProviderResponse
from app.core.config import Settings

PRODUCTS = {
    "laptop": "laptops", "notebook": "laptops", "monitor": "monitors", "display": "monitors", "screen": "monitors",
    "dock": "peripherals", "docking station": "peripherals", "keyboard": "peripherals", "mouse": "peripherals", "mice": "peripherals",
    "headset": "peripherals", "chair": "furniture", "desk": "furniture", "license": "software", "licence": "software",
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, s: Settings) -> None:
        self.model = "rules-v1"
        self.timeout_s = 5
        self._allowed = s.app_env != "production"

    @property
    def configured(self) -> bool:
        return self._allowed

    async def complete(self, system, messages, *, max_tokens, json_schema):
        if not self._allowed:
            raise LLMError(LLMErrorKind.NOT_CONFIGURED, "mock provider is disabled in production", self.name)
        prompt = messages[0].content
        if "Intent Agent" in system:
            out = _intent(prompt)
        elif "Research Agent" in system:
            out = _research(prompt)
        elif "Evaluation Agent" in system:
            out = _explain(prompt)
        else:
            out = {"summary": "OK"} if json_schema else "OK"
        text = out if isinstance(out, str) else json.dumps(out)
        return ProviderResponse(text, len(prompt) // 4, len(text) // 4)


def _intent(prompt: str) -> dict:
    today = date.fromisoformat(re.search(r"Today is (\d{4}-\d{2}-\d{2})", prompt).group(1))
    text = re.search(r"Request text: (.*)", prompt).group(1)
    low = text.lower()
    product = next((p for p in sorted(PRODUCTS, key=len, reverse=True) if p in low), None)
    qty = None
    for m in re.finditer(r"(?<![$\d,.])(\d{1,6})(?![\d,.]*\s*(?:gb|tb|k\b|%|\"|in))", low):
        qty = int(m.group(1))
        break
    specs = {}
    if ram := re.search(r"(\d+)\s*gb", low):
        specs["ram_gb_min"] = int(ram.group(1))
    budget = None
    if b := re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(k)?", low):
        budget = float(b.group(1).replace(",", "")) * (1000 if b.group(2) else 1)
    deadline = None
    if d := re.search(r"by\s+([a-z]{3})[a-z]*\.?\s+(\d{1,2})", low):
        month, day = MONTHS.get(d.group(1)), int(d.group(2))
        if month:
            deadline = date(today.year, month, day)
            if deadline < today:
                deadline = date(today.year + 1, month, day)
    questions = []
    if not product:
        questions.append("Which product do you need?")
    if qty is None:
        questions.append("How many units do you need?")
    if budget is None:
        questions.append("What is your budget?")
    items = [{"product_name": product, "quantity": qty, "specifications": specs, "category": PRODUCTS[product]}] if product else []
    return {"title": f"{qty} × {product}" if product and qty else None, "items": items, "budget": budget, "currency": "USD",
            "deadline": deadline.isoformat() if deadline else None, "clarification_questions": questions}


def _research(prompt: str) -> dict:
    specs = ast.literal_eval(re.search(r"specifications: (\{.*?\}|none)\n", prompt).group(1).replace("none", "{}"))
    wanted = re.search(r"× (.*?);", prompt).group(1).lower()
    products = ast.literal_eval(prompt.split("Catalogue products:\n", 1)[1])
    matches = []
    for p in products:
        kind_ok = any(w.rstrip("s") in (p["name"] + " " + p["category"]).lower() for w in wanted.split())
        misses = []
        for key, limit in specs.items():
            base, _, bound = key.rpartition("_")
            value = p["specs"].get(base)
            if bound == "min" and (value is None or value < limit):
                misses.append(f"{base} {value} < {limit}")
            if bound == "max" and (value is None or value > limit):
                misses.append(f"{base} {value} > {limit}")
        ok = kind_ok and not misses
        reason = "meets all specifications" if ok else ("different kind of product" if not kind_ok else "; ".join(misses))
        matches.append({"sku": p["sku"], "meets_requirements": ok, "reason": reason})
    return {"matches": matches}


def _explain(prompt: str) -> dict:
    ranking = ast.literal_eval(re.search(r"Ranking \(rank 1 is recommended\): (\[.*?\])\nExcluded", prompt, re.S).group(1))
    w = ranking[0]
    reasons = [f"{r['vendor']}: {r['total']} {r['currency']}, {r['lead_time_days']} days, rating {r['rating']}, score {r['score']}" for r in ranking[:3]]
    risks = [] if w["within_budget"] else [f"{w['vendor']} is over budget"]
    return {"summary": f"{w['vendor']} has the best overall score ({w['score']}/100), balancing total cost ({w['total']} {w['currency']}), "
                       f"delivery in {w['lead_time_days']} days and a {w['rating']} vendor rating.",
            "reasons": reasons, "risks": risks}
