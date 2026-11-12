"""Tool registry safeguards and the implemented tools, against the seeded catalogue."""

import asyncio
import uuid
from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.agents import AGENT_PERMISSIONS, NO_AGENT_TOOLS
from app.agents.core.tools import (
    IdempotencyKeyRequired, ToolArgumentError, ToolBusinessError, ToolContext, ToolLimitError,
    ToolPermissionError, ToolRegistry, ToolTimeoutError,
)
from app.db.session import SessionLocal
from app.models import AgentToolLog, Product, PurchaseOrder, Vendor, VendorOffer, WorkflowEvent
from app.tools import registry

ALL = frozenset(registry.tools)


@pytest.fixture
async def ctx(request_id, agent_run_id):
    async with SessionLocal() as work:
        yield ToolContext(db=work, request_id=request_id, agent_name="test", agent_run_id=agent_run_id, allowed_tools=ALL, max_tool_calls=50)


async def offer_id(db, vendor: str, sku: str) -> uuid.UUID:
    return await db.scalar(select(VendorOffer.id).join(Vendor).join(Product).where(Vendor.name == vendor, Product.sku == sku))


async def product_id(db, sku: str) -> uuid.UUID:
    return await db.scalar(select(Product.id).where(Product.sku == sku))


async def logs(db, run_id) -> list[AgentToolLog]:
    return list((await db.scalars(select(AgentToolLog).where(AgentToolLog.agent_run_id == run_id).order_by(AgentToolLog.created_at))).all())


# ---------- registry contents ----------

def test_registry_has_all_phase6_tools_with_schemas():
    expected = {
        "search_catalog", "get_product_details", "search_vendors", "get_vendor_quotes", "check_availability", "compare_offers",
        "validate_requirements", "calculate_purchase_total", "create_purchase_order", "get_purchase_order", "update_purchase_order",
        "validate_budget", "validate_currency", "prepare_payment_request", "check_approval_status",
        "prepare_paypal_order", "get_paypal_order", "capture_approved_payment", "verify_paypal_capture", "retrieve_payment_status",
    }
    assert set(registry.tools) == expected
    for t in registry.tools.values():
        assert t.description and issubclass(t.args_model, BaseModel) and issubclass(t.result_model, BaseModel)
    writes = {n for n, t in registry.tools.items() if t.is_write}
    assert writes == {"create_purchase_order", "update_purchase_order", "prepare_payment_request", "prepare_paypal_order", "capture_approved_payment"}


def test_permissions_reference_real_tools_and_never_grant_money_movement():
    granted = set().union(*AGENT_PERMISSIONS.values())
    assert granted <= set(registry.tools)
    assert not granted & NO_AGENT_TOOLS
    assert set(AGENT_PERMISSIONS) == {"intent", "research", "evaluation", "procurement", "finance", "reconciliation"}


# ---------- safeguards ----------

async def test_disallowed_tool_is_blocked_and_logged(db, ctx):
    ctx.allowed_tools = frozenset({"search_catalog"})
    with pytest.raises(ToolPermissionError):
        await registry.execute(ctx, "get_vendor_quotes", {"product_id": str(uuid.uuid4()), "quantity": 1})
    [row] = await logs(db, ctx.agent_run_id)
    assert (row.tool_name, row.status) == ("get_vendor_quotes", "BLOCKED")
    assert ctx.calls_made == 0


async def test_invalid_arguments_rejected_and_logged(db, ctx):
    with pytest.raises(ToolArgumentError, match="quantity"):
        await registry.execute(ctx, "get_vendor_quotes", {"product_id": str(uuid.uuid4()), "quantity": 0})
    [row] = await logs(db, ctx.agent_run_id)
    assert row.status == "FAILED" and "quantity" in row.error


async def test_tool_call_limit(db, ctx):
    ctx.max_tool_calls = 2
    for _ in range(2):
        await registry.execute(ctx, "search_catalog", {"query": "mouse"})
    with pytest.raises(ToolLimitError):
        await registry.execute(ctx, "search_catalog", {"query": "mouse"})
    assert [r.status for r in await logs(db, ctx.agent_run_id)] == ["SUCCEEDED", "SUCCEEDED", "BLOCKED"]


async def test_tool_timeout(db, request_id, agent_run_id):
    reg = ToolRegistry()

    class Nothing(BaseModel):
        pass

    @reg.tool("slow", args=Nothing, result=Nothing, description="sleeps", timeout_s=0.05)
    async def slow(ctx, a):
        await asyncio.sleep(1)
        return Nothing()

    async with SessionLocal() as work:
        c = ToolContext(db=work, request_id=request_id, agent_name="t", agent_run_id=agent_run_id, allowed_tools=frozenset({"slow"}))
        with pytest.raises(ToolTimeoutError):
            await reg.execute(c, "slow", {})
    [row] = await logs(db, agent_run_id)
    assert row.status == "TIMED_OUT" and row.duration_ms < 1000


async def test_money_moving_tools_refuse_even_if_granted(db, ctx):
    for name, args in (("prepare_paypal_order", {"payment_id": str(uuid.uuid4())}), ("capture_approved_payment", {"paypal_order_id": "X1"})):
        with pytest.raises(ToolBusinessError, match="Agents cannot"):
            await registry.execute(ctx, name, args, idempotency_key=f"k-{uuid.uuid4()}")
    assert [r.status for r in await logs(db, ctx.agent_run_id)] == ["FAILED", "FAILED"]


async def test_only_reconciliation_can_mark_paid(db, ctx):
    oid = await offer_id(db, "Fabrikam Office", "KB-WL")
    po = await registry.execute(ctx, "create_purchase_order", {"lines": [{"offer_id": str(oid), "quantity": 2}]}, idempotency_key=f"k-{uuid.uuid4()}")
    with pytest.raises(ToolBusinessError, match="Only the Reconciliation Agent"):
        await registry.execute(ctx, "update_purchase_order", {"purchase_order_id": str(po.purchase_order_id), "status": "PAID"}, idempotency_key=f"k-{uuid.uuid4()}")
    ctx.agent_name = "reconciliation"
    with pytest.raises(ToolBusinessError, match="only APPROVED"):
        await registry.execute(ctx, "update_purchase_order", {"purchase_order_id": str(po.purchase_order_id), "status": "PAID"}, idempotency_key=f"k-{uuid.uuid4()}")
    for status in ("APPROVED", "REJECTED"):
        with pytest.raises(ToolBusinessError, match="human approver"):
            await registry.execute(ctx, "update_purchase_order", {"purchase_order_id": str(po.purchase_order_id), "status": status}, idempotency_key=f"k-{uuid.uuid4()}")


async def test_every_call_emits_a_workflow_event(db, ctx):
    await registry.execute(ctx, "search_catalog", {"category": "monitors"})
    ev = await db.scalar(select(WorkflowEvent).where(WorkflowEvent.request_id == ctx.request_id, WorkflowEvent.event_type == "agent.tool_called"))
    assert ev.payload["tool"] == "search_catalog" and ev.payload["status"] == "SUCCEEDED"


# ---------- catalogue tools ----------

async def test_search_catalog_matches_words_and_returns_sources(ctx):
    res = await registry.execute(ctx, "search_catalog", {"query": "laptop 16GB"})
    assert {p.sku for p in res.products} >= {"LAP-PRO-14", "LAP-ULTRA-16", "LAP-ESS-13"}
    assert all(p.source["table"] == "products" for p in res.products)


async def test_vendor_quotes_flag_expired_moq_and_sort(db, ctx):
    res = await registry.execute(ctx, "get_vendor_quotes", {"product_id": str(await product_id(db, "LAP-ESS-13")), "quantity": 5})
    by_vendor = {q.vendor_name: q for q in res.quotes}
    contoso = by_vendor["Contoso Devices"]
    assert not contoso.eligible and any("expired" in i for i in contoso.issues) and any("minimum order" in i for i in contoso.issues)
    assert res.eligible_count == 2
    assert [q.eligible for q in res.quotes] == [True, True, False]  # eligible first
    assert res.quotes[0].line_total == Decimal("3145.00")  # Fabrikam 629.00 × 5


async def test_check_availability_out_of_stock(db, ctx):
    res = await registry.execute(ctx, "check_availability", {"offer_id": str(await offer_id(db, "Contoso Devices", "DOCK-USBC")), "quantity": 1})
    assert not res.available and "only 0 in stock" in res.issues


async def test_compare_offers(db, ctx):
    ids = [await offer_id(db, v, "MON-27-4K") for v in ("Northwind IT Supply", "Contoso Devices", "Fabrikam Office")]
    res = await registry.execute(ctx, "compare_offers", {"offer_ids": [str(i) for i in ids], "quantity": 4})
    assert res.cheapest_offer_id == ids[1]  # Contoso 399 (MOQ 2 ok)
    assert res.fastest_offer_id == ids[2]  # Fabrikam 2 days
    assert res.best_rated_offer_id == ids[0]  # Northwind 4.6


async def test_unknown_product_is_business_error(ctx):
    with pytest.raises(ToolBusinessError):
        await registry.execute(ctx, "get_product_details", {"sku": "NOPE"})


# ---------- procurement / finance tools ----------

async def test_totals_use_db_prices_and_decimal_rounding(db, ctx):
    oid = await offer_id(db, "Contoso Devices", "LAP-PRO-14")
    res = await registry.execute(ctx, "calculate_purchase_total", {
        "lines": [{"offer_id": str(oid), "quantity": 5, "unit_price": "1.00"}],  # a caller-supplied price is ignored
        "tax_rate": "0.0825",
    })
    assert res.lines[0].unit_price == Decimal("1099.00")
    assert (res.subtotal, res.tax_amount, res.total) == (Decimal("5495.00"), Decimal("453.34"), Decimal("5948.34"))


async def test_create_po_is_idempotent(db, ctx):
    oid = await offer_id(db, "Northwind IT Supply", "LAP-PRO-14")
    args = {"lines": [{"offer_id": str(oid), "quantity": 3}]}
    key = f"{ctx.request_id}:procurement:create_po"

    with pytest.raises(IdempotencyKeyRequired):
        await registry.execute(ctx, "create_purchase_order", args)
    first = await registry.execute(ctx, "create_purchase_order", args, idempotency_key=key)
    second = await registry.execute(ctx, "create_purchase_order", args, idempotency_key=key)

    assert first.purchase_order_id == second.purchase_order_id and first.total == Decimal("3447.00")
    assert first.po_number.startswith("PO-")
    assert await db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.request_id == ctx.request_id)) == 1
    assert [r.status for r in await logs(db, ctx.agent_run_id)] == ["FAILED", "SUCCEEDED", "DUPLICATE"]


async def test_create_po_refuses_unavailable_or_mixed_vendor_lines(db, ctx):
    expired = await offer_id(db, "Contoso Devices", "LAP-ESS-13")
    with pytest.raises(ToolBusinessError, match="unavailable"):
        await registry.execute(ctx, "create_purchase_order", {"lines": [{"offer_id": str(expired), "quantity": 10}]}, idempotency_key=f"k-{uuid.uuid4()}")
    a, b = await offer_id(db, "Northwind IT Supply", "KB-WL"), await offer_id(db, "Fabrikam Office", "MS-ERGO")
    with pytest.raises(ToolBusinessError, match="exactly one vendor"):
        await registry.execute(ctx, "create_purchase_order", {"lines": [{"offer_id": str(a), "quantity": 1}, {"offer_id": str(b), "quantity": 1}]}, idempotency_key=f"k-{uuid.uuid4()}")
    assert await db.scalar(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.request_id == ctx.request_id)) == 0


async def test_po_of_another_request_is_invisible(db, ctx, request_id):
    oid = await offer_id(db, "Fabrikam Office", "KB-WL")
    po = await registry.execute(ctx, "create_purchase_order", {"lines": [{"offer_id": str(oid), "quantity": 2}]}, idempotency_key=f"k-{uuid.uuid4()}")
    got = await registry.execute(ctx, "get_purchase_order", {})
    assert got.purchase_order_id == po.purchase_order_id and got.lines[0].quantity == 2

    ctx.request_id = uuid.uuid4()  # pretend to be an agent working on a different request
    with pytest.raises(ToolBusinessError, match="not found"):
        await registry.execute(ctx, "get_purchase_order", {"purchase_order_id": str(po.purchase_order_id)})


async def test_validate_requirements_and_budget(ctx):
    r = await registry.execute(ctx, "validate_requirements", {"items": [{"product_name": "laptop", "quantity": 5}], "currency": "JPY", "deadline": "2020-01-01"})
    assert not r.valid and r.missing_fields == ["budget"] and len(r.issues) == 2

    ok = await registry.execute(ctx, "validate_budget", {"amount": "9999.99", "currency": "USD"})
    assert ok.within_budget and ok.remaining == Decimal("0.01")
    over = await registry.execute(ctx, "validate_budget", {"amount": "10000.01", "currency": "USD"})
    assert not over.within_budget and "exceeds budget" in over.issues[0]

    cur = await registry.execute(ctx, "validate_currency", {"currency": "eur"})
    assert cur.supported and not cur.matches_request


async def test_approval_and_payment_status_reads(ctx):
    status = await registry.execute(ctx, "check_approval_status", {})
    assert not status.approved and status.latest_decision is None
    pay = await registry.execute(ctx, "retrieve_payment_status", {})
    assert pay.payments == []
