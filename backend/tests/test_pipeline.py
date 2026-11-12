"""End-to-end agent pipeline against the seeded catalogue, with a scripted LLM (no network).

Covers the acceptance example and the Phase 12.2 agent cases.
"""

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.agents.core.runner import AgentRunner
from app.agents.intent import IntentAgent
from app.agents.pipeline import ProcurementPipeline, WorkflowExecutor
from app.ai.errors import LLMErrorKind as K
from app.ai.gateway import LLMGateway, db_recorder
from app.db.session import SessionLocal
from app.models import (
    AgentRun, ModelExecution, ProcurementRequest, PurchaseOrder, PurchaseOrderItem, User, Vendor, VendorOffer, WorkflowEvent,
    WorkflowStateHistory,
)
from app.models.enums import WorkflowState as S
from app.tools import registry
from app.workflow import Actor, recover_interrupted, register_resumer, transition
from app.workflow.recovery import clear_resumers
from seed.data import DEMO_USER_EMAIL
from tests.fakes import RoutingProvider, Sleep, err, test_settings

TODAY = date.today()


def laptops(qty=10, budget=15000, deadline_days=19, ram=16, **extra):
    return {"title": "New hire laptops", "budget": budget, "currency": "USD",
            "deadline": (TODAY + timedelta(days=deadline_days)).isoformat() if deadline_days is not None else None,
            "items": [{"product_name": "laptop", "quantity": qty, "specifications": {"ram_gb_min": ram}, "category": "laptops"}], **extra}


LAPTOP_MATCH = {"matches": [
    {"sku": "LAP-PRO-14", "meets_requirements": True, "reason": "16GB RAM meets the minimum"},
    {"sku": "LAP-ULTRA-16", "meets_requirements": True, "reason": "32GB RAM exceeds the minimum"},
    {"sku": "LAP-ESS-13", "meets_requirements": False, "reason": "only 8GB RAM"},
]}
EXPLAIN = {"summary": "Fabrikam wins on delivery speed with a competitive price.", "reasons": ["fastest"], "risks": []}


def routes(intent=None, research=LAPTOP_MATCH, explain=EXPLAIN):
    return {"Intent Agent": intent if intent is not None else laptops(), "Research Agent": research, "Evaluation Agent": explain}


def pipeline_with(*providers) -> ProcurementPipeline:
    gw = LLMGateway(list(providers), settings=test_settings(), recorder=db_recorder)
    return ProcurementPipeline(runner=AgentRunner(gateway=gw))


async def new_request(db, text="10 laptops with 16GB RAM, under $15,000 by Dec 1", **fields) -> uuid.UUID:
    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    pr = ProcurementRequest(requester_id=user.id, raw_text=text, **fields)
    db.add(pr)
    await db.commit()
    await transition(db, pr.id, S.ANALYZING, actor=Actor.user(user.id))
    return pr.id


async def run(db, routes_or_providers, **request_fields):
    providers = routes_or_providers if isinstance(routes_or_providers, list) else [RoutingProvider(routes_or_providers)]
    rid = await new_request(db, **request_fields)
    final = await pipeline_with(*providers).run(rid)
    return rid, final, providers[-1]


async def last_reason(db, rid) -> str:
    db.expire_all()
    h = await db.scalar(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == rid).order_by(WorkflowStateHistory.created_at.desc()).limit(1))
    return h.reason or ""


async def latest_output(db, rid, agent):
    db.expire_all()
    run_ = await db.scalar(select(AgentRun).where(AgentRun.request_id == rid, AgentRun.agent_name == agent).order_by(AgentRun.created_at.desc()).limit(1))
    return run_.output if run_ else None


# ---------- acceptance ----------

async def test_laptop_request_produces_po_awaiting_approval(db):
    rid, final, llm = await run(db, routes())
    assert final == S.AWAITING_APPROVAL, await last_reason(db, rid)

    po = await db.scalar(select(PurchaseOrder).where(PurchaseOrder.request_id == rid))
    items = (await db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id))).all()
    vendor = await db.get(Vendor, po.vendor_id)
    offer = await db.get(VendorOffer, items[0].offer_id)

    # Fabrikam: 1189.00 × 10, 3-day delivery → best combined score (see evaluation weights).
    assert vendor.name == "Fabrikam Office" and po.status == "PENDING_APPROVAL"
    assert (items[0].quantity, items[0].unit_price, po.total) == (10, Decimal("1189.00"), Decimal("11890.00"))
    assert offer.vendor_id == po.vendor_id and items[0].line_total == offer.unit_price * 10  # a real offer, priced from the DB

    pr = await db.get(ProcurementRequest, rid)
    assert pr.requirements["items"][0]["quantity"] == 10 and pr.requirements["budget"] == "15000"
    assert pr.title == "New hire laptops"

    ev = (await latest_output(db, rid, "evaluation"))
    assert [c["vendor_name"] for c in ev["ranking"]] == ["Fabrikam Office", "Northwind IT Supply", "Contoso Devices"]
    assert ev["explanation_source"] == "llm:openai"

    states = [h.to_state for h in (await db.scalars(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == rid).order_by(WorkflowStateHistory.created_at))).all()]
    assert states == ["ANALYZING", "RESEARCHING", "EVALUATING", "ORDER_DRAFTED", "FINANCE_VALIDATING", "AWAITING_APPROVAL"]
    types = [e.event_type for e in (await db.scalars(select(WorkflowEvent).where(WorkflowEvent.request_id == rid).order_by(WorkflowEvent.seq))).all()]
    assert types[-1] == "approval.required"
    assert types.count("agent.completed") == 5 and "agent.failed" not in types
    assert llm.calls_for("Intent Agent") == 1 and llm.calls_for("Research Agent") == 1 and llm.calls_for("Evaluation Agent") == 1


async def test_form_fields_override_the_text(db):
    rid, final, _ = await run(db, routes(intent=laptops(budget=99999)), budget=Decimal("14000"))
    assert final == S.AWAITING_APPROVAL
    assert (await db.get(ProcurementRequest, rid)).requirements["budget"] == "14000.00"


# ---------- Phase 12.2 cases ----------

async def test_missing_product_details_asks_for_clarification(db):
    rid, final, _ = await run(db, routes(intent={"items": [], "budget": 500, "clarification_questions": ["What do you want to buy?"]}), text="buy some stuff")
    assert final == S.FAILED
    out = await latest_output(db, rid, "intent")
    assert out["outcome"] == "clarify" and "Which product or service do you need?" in out["clarification_questions"]
    assert "What do you want to buy?" in out["clarification_questions"]
    assert await db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.request_id == rid)) == 0


async def test_invalid_quantity(db):
    rid, final, _ = await run(db, routes(intent=laptops(qty=0)))
    assert final == S.FAILED and "invalid quantity 0" in await last_reason(db, rid)


async def test_missing_budget(db):
    rid, final, _ = await run(db, routes(intent=laptops(budget=None)))
    assert final == S.FAILED
    out = await latest_output(db, rid, "intent")
    assert "What is the maximum budget for this purchase?" in out["clarification_questions"]


async def test_past_deadline(db):
    rid, final, _ = await run(db, routes(intent=laptops(deadline_days=-3)))
    assert final == S.FAILED and "deadline" in await last_reason(db, rid)


async def test_no_matching_product(db):
    intent = {"items": [{"product_name": "forklift", "quantity": 1}], "budget": 30000, "currency": "USD"}
    rid, final, llm = await run(db, routes(intent=intent))
    assert final == S.REJECTED and "No product in the catalogue matches 'forklift'" in await last_reason(db, rid)
    assert llm.calls_for("Research Agent") == 0  # nothing to judge — no LLM call needed


async def test_specs_no_product_meets(db):
    research = {"matches": [{"sku": s, "meets_requirements": False, "reason": "max 32GB"} for s in ("LAP-PRO-14", "LAP-ULTRA-16", "LAP-ESS-13")]}
    rid, final, _ = await run(db, routes(intent=laptops(ram=128), research=research))
    assert final == S.REJECTED and "No catalogue product meets the specifications" in await last_reason(db, rid)


async def test_no_available_vendor(db):
    intent = {"items": [{"product_name": "docking station", "quantity": 500, "category": "peripherals"}], "budget": 200000, "currency": "USD"}
    research = {"matches": [{"sku": "DOCK-USBC", "meets_requirements": True, "reason": "USB-C dock"}]}
    rid, final, _ = await run(db, routes(intent=intent, research=research))
    reason = await last_reason(db, rid)
    assert final == S.REJECTED and "No vendor can currently supply 500 × docking station" in reason
    assert "only 0 in stock" in reason and "only 70 in stock" in reason


async def test_fabricated_sku_is_discarded(db):
    research = {"matches": [*LAPTOP_MATCH["matches"], {"sku": "LAP-FAKE-99", "meets_requirements": True, "reason": "made up"}]}
    rid, final, _ = await run(db, routes(research=research))
    assert final == S.AWAITING_APPROVAL
    out = await latest_output(db, rid, "research")
    assert out["items"][0]["discarded_unknown_skus"] == ["LAP-FAKE-99"]
    assert all(q["sku"] != "LAP-FAKE-99" for q in out["items"][0]["quotes"])


async def test_conflicting_offers_ranked_deterministically_with_rules_fallback(db):
    # Keyboards: Fabrikam cheaper+faster, Northwind better rated. The LLM can't explain → rules explanation.
    intent = {"items": [{"product_name": "wireless keyboard", "quantity": 20, "category": "peripherals"}], "budget": 2000, "currency": "USD"}
    research = {"matches": [{"sku": "KB-WL", "meets_requirements": True, "reason": "wireless keyboard"}]}
    rid, final, _ = await run(db, routes(intent=intent, research=research, explain="not json at all"))
    assert final == S.AWAITING_APPROVAL
    ev = await latest_output(db, rid, "evaluation")
    assert [c["vendor_name"] for c in ev["ranking"]] == ["Fabrikam Office", "Northwind IT Supply"]
    assert ev["explanation_source"] == "rules" and "Fabrikam Office" in ev["explanation"]["summary"]
    rerun = await run(db, routes(intent=intent, research=research, explain="still not json"))
    assert [c["vendor_name"] for c in (await latest_output(db, rerun[0], "evaluation"))["ranking"]] == ["Fabrikam Office", "Northwind IT Supply"]


async def test_multi_item_needs_single_vendor(db):
    intent = {"items": [{"product_name": "keyboard", "quantity": 5, "category": "peripherals"},
                        {"product_name": "ergonomic chair", "quantity": 5, "category": "furniture"}], "budget": 5000, "currency": "USD"}

    def research(prompt):
        sku = "KB-WL" if "keyboard" in prompt.split("Catalogue")[0] else "CHAIR-ERGO"
        return {"matches": [{"sku": sku, "meets_requirements": True, "reason": "fits"}]}

    rid, final, _ = await run(db, routes(intent=intent, research=research))
    # Only Fabrikam sells both keyboards and chairs.
    assert final == S.AWAITING_APPROVAL
    po = await db.scalar(select(PurchaseOrder).where(PurchaseOrder.request_id == rid))
    assert (await db.get(Vendor, po.vendor_id)).name == "Fabrikam Office"
    assert po.total == Decimal("69.00") * 5 + Decimal("429.00") * 5
    ev = await latest_output(db, rid, "evaluation")
    assert any("Northwind IT Supply: cannot supply ergonomic chair" in x for x in ev["excluded"])


async def test_deadline_nobody_can_meet(db):
    rid, final, _ = await run(db, routes(intent=laptops(deadline_days=1)))
    assert final == S.REJECTED and "No vendor can deliver by" in await last_reason(db, rid)


async def test_finance_blocks_over_budget(db):
    rid, final, _ = await run(db, routes(intent=laptops(budget=5000)))
    assert final == S.REJECTED
    reason = await last_reason(db, rid)
    assert reason.startswith("Finance blocked the purchase") and "exceeds budget" in reason
    po = await db.scalar(select(PurchaseOrder).where(PurchaseOrder.request_id == rid))
    assert po.status == "CANCELLED" and "Blocked by finance" in po.notes
    fin = await latest_output(db, rid, "finance")
    assert [c["name"] for c in fin["report"]["checks"] if not c["passed"]] == ["budget"]


async def test_agent_timeout_fails_request(db, monkeypatch):
    monkeypatch.setattr(IntentAgent, "timeout_s", 0.1)
    rid, final, _ = await run(db, routes(intent=Sleep(2, "{}")))
    assert final == S.FAILED and "intent agent timed_out" in await last_reason(db, rid)


async def test_llm_fallback_to_openai(db):
    cloud = RoutingProvider({"Agent": err(K.UNAVAILABLE)}, name="anthropic")
    local = RoutingProvider(routes())
    rid, final, _ = await run(db, [cloud, local])
    assert final == S.AWAITING_APPROVAL
    runs = (await db.scalars(select(AgentRun.id).where(AgentRun.request_id == rid))).all()
    execs = (await db.scalars(select(ModelExecution).where(ModelExecution.agent_run_id.in_(runs)))).all()
    assert {e.provider for e in execs if e.success} == {"openai"} and all(e.fallback_used for e in execs if e.success)


async def test_all_llms_down_fails_request(db):
    rid, final, _ = await run(db, [RoutingProvider({"Agent": err(K.UNAVAILABLE)})])
    assert final == S.FAILED and "All LLM providers failed" in await last_reason(db, rid)


async def test_tool_failure_fails_request(db, monkeypatch):
    async def broken(ctx, a):
        raise ConnectionError("catalogue service down")

    monkeypatch.setattr(registry.tools["get_vendor_quotes"], "handler", broken)
    rid, final, _ = await run(db, routes())
    assert final == S.FAILED and "research agent failed" in (await last_reason(db, rid)).lower()


# ---------- cancellation, resume, idempotency ----------

async def test_cancel_while_running_stops_before_next_agent(db):
    llm = RoutingProvider(routes(research=Sleep(0.5, LAPTOP_MATCH)))
    rid = await new_request(db)
    ex = WorkflowExecutor()
    ex.pipeline_factory = lambda: pipeline_with(llm)
    ex.start(rid)
    for _ in range(100):  # wait until research is underway
        await asyncio.sleep(0.05)
        if llm.calls_for("Research Agent"):
            break
    async with SessionLocal() as s:
        await transition(s, rid, S.CANCELLED, actor=Actor.system("test"))
    await ex.wait(rid)
    assert (await db.get(ProcurementRequest, rid, populate_existing=True)).status == S.CANCELLED
    assert llm.calls_for("Evaluation Agent") == 0
    assert await db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.request_id == rid)) == 0


async def test_resume_after_restart_skips_finished_agents(db):
    llm = RoutingProvider(routes())
    # Run only the intent step (as the pipeline would, including saving its requirements),
    # then leave the request in RESEARCHING as if the server died there.
    rid = await new_request(db)
    p = pipeline_with(llm)
    res = await p.runner.run(IntentAgent(), rid, {"request": {"raw_text": "10 laptops", "title": None}, "today": TODAY.isoformat()})
    from app.agents.intent import IntentResult
    await p._save_requirements(rid, IntentResult.model_validate(res.output))
    async with SessionLocal() as s:
        await transition(s, rid, S.RESEARCHING, actor=Actor.agent("intent"))
    assert llm.calls_for("Intent Agent") == 1

    ex = WorkflowExecutor()
    ex.pipeline_factory = lambda: pipeline_with(llm)
    clear_resumers()
    register_resumer({S.RESEARCHING}, lambda r, st: _start(ex, r, st))
    try:
        report = await recover_interrupted(SessionLocal)
        assert rid in report.resumed
        await ex.wait(rid)
    finally:
        clear_resumers()
    assert (await db.get(ProcurementRequest, rid, populate_existing=True)).status == S.AWAITING_APPROVAL
    assert llm.calls_for("Intent Agent") == 1  # not re-run


async def _start(ex, rid, state):
    ex.start(rid, state)


async def test_rerun_reuses_same_purchase_order(db):
    llm = RoutingProvider(routes())
    rid = await new_request(db)
    p = pipeline_with(llm)
    await p.run(rid)
    po_ids = (await db.scalars(select(PurchaseOrder.id).where(PurchaseOrder.request_id == rid))).all()
    # Simulate a crash right after the PO was drafted, then resume from EVALUATING: same lines → same PO.
    await db.execute(PurchaseOrder.__table__.update().where(PurchaseOrder.request_id == rid).values(status="DRAFT"))
    await db.execute(ProcurementRequest.__table__.update().where(ProcurementRequest.id == rid).values(status="EVALUATING"))
    await db.commit()
    assert await p.run(rid, S.EVALUATING) == S.AWAITING_APPROVAL
    assert (await db.scalars(select(PurchaseOrder.id).where(PurchaseOrder.request_id == rid))).all() == po_ids
