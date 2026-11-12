"""API v1 tests: health, catalogue, and persisted procurement requests."""

import uuid

from httpx import AsyncClient


async def test_health(client: AsyncClient):
    body = (await client.get("/api/v1/health")).json()
    assert body["database"] is True
    assert body["schema_version"]


async def test_old_unversioned_paths_are_gone(client: AsyncClient):
    assert (await client.get("/api/health")).status_code == 404


async def test_vendors_and_detail(client: AsyncClient):
    vendors = (await client.get("/api/v1/vendors")).json()
    names = {v["name"] for v in vendors}
    assert {"Northwind IT Supply", "Contoso Devices"} <= names
    northwind = next(v for v in vendors if v["name"] == "Northwind IT Supply")
    detail = (await client.get(f"/api/v1/vendors/{northwind['id']}")).json()
    assert len(detail["offers"]) >= 5
    assert (await client.get(f"/api/v1/vendors/{uuid.uuid4()}")).status_code == 404


async def test_products_with_offers_and_money_as_strings(client: AsyncClient):
    laptops = (await client.get("/api/v1/products", params={"category": "laptops"})).json()
    assert {p["sku"] for p in laptops} >= {"LAP-PRO-14", "LAP-ULTRA-16", "LAP-ESS-13"}
    pro = next(p for p in laptops if p["sku"] == "LAP-PRO-14")
    prices = [o["unit_price"] for o in pro["offers"]]
    assert prices == sorted(prices, key=float) and all(isinstance(x, str) for x in prices)
    assert prices[0] == "1099.00"

    ess = next(p for p in laptops if p["sku"] == "LAP-ESS-13")
    assert any(o["is_expired"] for o in ess["offers"])

    hits = (await client.get("/api/v1/products", params={"q": "monitor"})).json()
    assert hits and all(p["category"] == "monitors" for p in hits)


async def test_create_and_read_procurement_request(client: AsyncClient):
    r = await client.post("/api/v1/procurement/requests", json={
        "raw_text": "10 laptops with 16GB RAM for new hires by Dec 1",
        "title": "New hire laptops", "budget": "15000.00", "deadline": "2026-12-01",
    })
    assert r.status_code == 201, r.text
    pr = r.json()
    assert pr["status"] == "CREATED" and pr["budget"] == "15000.00"

    got = (await client.get(f"/api/v1/procurement/requests/{pr['id']}")).json()
    assert got["raw_text"].startswith("10 laptops")
    listed = (await client.get("/api/v1/procurement/requests")).json()
    assert pr["id"] in {x["id"] for x in listed}


async def test_request_validation(client: AsyncClient):
    assert (await client.post("/api/v1/procurement/requests", json={"raw_text": "hi"})).status_code == 422
    assert (await client.post("/api/v1/procurement/requests", json={"raw_text": "valid text", "budget": -1})).status_code == 422
    assert (await client.post("/api/v1/procurement/requests", json={"raw_text": "valid text", "currency": "usd"})).status_code == 422
    assert (await client.post("/api/v1/procurement/requests", json={"raw_text": "valid text", "budget": "1.234"})).status_code == 422
    assert (await client.get(f"/api/v1/procurement/requests/{uuid.uuid4()}")).status_code == 404


async def test_request_creation_writes_history_and_audit(client: AsyncClient, db):
    from sqlalchemy import select

    from app.models import AuditLog, WorkflowStateHistory

    pr = (await client.post("/api/v1/procurement/requests", json={"raw_text": "5 ergonomic chairs"})).json()
    rid = uuid.UUID(pr["id"])
    hist = (await db.scalars(select(WorkflowStateHistory).where(WorkflowStateHistory.request_id == rid))).all()
    assert [(h.from_state, h.to_state) for h in hist] == [(None, "CREATED")]
    audit = await db.scalar(select(AuditLog).where(AuditLog.entity_id == rid))
    assert audit.action == "procurement_request.created"
