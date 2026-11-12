"""Agent runner: run persistence, events, timeouts, failures, LLM + tool integration."""

import asyncio

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.agents import AgentContext, AgentRunner, BaseAgent
from app.ai.errors import LLMErrorKind as K
from app.ai.gateway import LLMGateway, db_recorder
from app.models import AgentRun, AgentToolLog, ModelExecution, WorkflowEvent
from tests.fakes import FakeProvider, err, test_settings


class Pick(BaseModel):
    sku: str
    reason: str


def runner(*providers) -> AgentRunner:
    providers = providers or (FakeProvider("openai", ['{"sku": "LAP-PRO-14", "reason": "cheapest fit"}']),)
    return AgentRunner(gateway=LLMGateway(list(providers), settings=test_settings(), recorder=db_recorder))


class ResearchLike(BaseAgent):
    name = "research"
    system_prompt = "Pick a product."

    async def run(self, ctx: AgentContext):
        found = await ctx.tool("search_catalog", {"category": "laptops"})
        gen = await ctx.generate(Pick, prompt=f"Options: {[p.sku for p in found.products]}")
        ctx.notes.append(f"chose via {gen.provider}")
        return {"pick": gen.data.model_dump(), "options": len(found.products)}


async def events(db, request_id) -> list[str]:
    rows = (await db.scalars(select(WorkflowEvent).where(WorkflowEvent.request_id == request_id).order_by(WorkflowEvent.created_at))).all()
    return [r.event_type for r in rows]


async def test_successful_run_is_persisted_with_trace(db, request_id):
    res = await runner().run(ResearchLike(), request_id, {"hint": "laptops"})
    assert res.ok and res.tool_calls == 1
    assert res.output["pick"]["sku"] == "LAP-PRO-14" and res.output["options"] == 3
    assert res.output["_notes"] == ["chose via openai"]

    run = await db.get(AgentRun, res.agent_run_id)
    assert (run.status, run.agent_name, run.input) == ("SUCCEEDED", "research", {"hint": "laptops"})
    assert run.started_at and run.finished_at and run.finished_at >= run.started_at
    assert await events(db, request_id) == ["agent.started", "agent.tool_called", "agent.completed"]

    execs = (await db.scalars(select(ModelExecution).where(ModelExecution.agent_run_id == res.agent_run_id))).all()
    assert [(e.provider, e.success, e.purpose) for e in execs] == [("openai", True, "research")]


async def test_llm_fallback_inside_agent(db, request_id):
    res = await runner(FakeProvider("anthropic", [err(K.UNAVAILABLE)]),
                       FakeProvider("openai", ['{"sku": "X", "reason": "r"}'])).run(ResearchLike(), request_id)
    assert res.ok
    execs = (await db.scalars(select(ModelExecution).where(ModelExecution.agent_run_id == res.agent_run_id).order_by(ModelExecution.created_at))).all()
    assert [(e.provider, e.success, e.fallback_used) for e in execs] == [("anthropic", False, False), ("anthropic", False, False), ("openai", True, True)]


async def test_all_llms_down_fails_the_run(db, request_id):
    res = await runner(FakeProvider("openai", [err(K.UNAVAILABLE)])).run(ResearchLike(), request_id)
    assert res.status == "FAILED" and "All LLM providers failed" in res.error
    assert (await events(db, request_id))[-1] == "agent.failed"


async def test_agent_exception_becomes_failed_run(db, request_id):
    class Broken(BaseAgent):
        name = "intent"

        async def run(self, ctx):
            raise ValueError("bad parse")

    res = await runner().run(Broken(), request_id)
    assert (res.status, res.error) == ("FAILED", "ValueError: bad parse")
    run = await db.get(AgentRun, res.agent_run_id)
    assert run.status == "FAILED" and run.error == "ValueError: bad parse"


async def test_agent_timeout(db, request_id):
    class Slow(BaseAgent):
        name = "evaluation"
        timeout_s = 0.05

        async def run(self, ctx):
            await asyncio.sleep(1)

    res = await runner().run(Slow(), request_id)
    assert res.status == "TIMED_OUT" and res.duration_ms < 1000
    assert (await db.get(AgentRun, res.agent_run_id)).status == "TIMED_OUT"


async def test_agent_cannot_use_tools_outside_its_permissions(db, request_id):
    class Sneaky(BaseAgent):
        name = "intent"  # intent may not create purchase orders

        async def run(self, ctx):
            await ctx.tool("create_purchase_order", {"lines": []}, idempotency_key="x")

    res = await runner().run(Sneaky(), request_id)
    assert res.status == "FAILED" and "not permitted" in res.error
    [log] = (await db.scalars(select(AgentToolLog).where(AgentToolLog.agent_run_id == res.agent_run_id))).all()
    assert (log.tool_name, log.status) == ("create_purchase_order", "BLOCKED")


async def test_unknown_agent_has_no_tools(request_id):
    class Rogue(BaseAgent):
        name = "rogue"

        async def run(self, ctx):
            await ctx.tool("search_catalog", {})

    assert Rogue().allowed_tools == frozenset()
    res = await runner().run(Rogue(), request_id)
    assert res.status == "FAILED"


def test_idempotency_key_is_stable(request_id):
    from types import SimpleNamespace

    ctx = AgentContext(agent=ResearchLike(), request_id=request_id, agent_run_id=None, input={}, tool_ctx=SimpleNamespace(),
                       registry=None, gateway=None)
    assert ctx.idempotency_key("create_po") == f"{request_id}:research:create_po" == ctx.idempotency_key("create_po")


@pytest.mark.skipif("not config.getoption('--live')", reason="calls the real Claude API; run with --live")
async def test_live_claude_structured_output(request_id):
    from app.ai.providers import AnthropicProvider
    from app.core.config import Settings

    gw = LLMGateway([AnthropicProvider(Settings())], settings=test_settings(), recorder=None)
    res = await gw.generate(Pick, system="Pick the best laptop SKU for a designer.",
                            prompt="Options: LAP-PRO-14 (16GB), LAP-ULTRA-16 (32GB), LAP-ESS-13 (8GB)")
    assert res.provider == "anthropic" and res.data.sku.startswith("LAP-")
    print(f"\nlive claude ({res.model}): {res.latency_ms} ms → {res.data}")
