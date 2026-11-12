"""Request ids, structured logs, error handling, append-only history, insight endpoints, demo modes."""

import json
import logging
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

import app.payments.paypal as paypal_module
from app.agents.core.runner import AgentRunner
from app.agents.pipeline import ProcurementPipeline, executor
from app.ai.gateway import LLMGateway
from app.ai.mock import MockProvider
from app.api.deps import get_paypal
from app.core.observability import JsonFormatter, request_id_var
from app.db.session import get_session
from app.main import app
from app.models import AuditLog, WorkflowStateHistory
from tests.fakes import test_settings

LAPTOPS = "10 laptops, 16GB RAM, under $15,000 by Dec 1"


# ---------- request ids & logs ----------

async def test_every_response_has_a_request_id(anon_client: AsyncClient):
    r = await anon_client.get("/api/v1/health")
    assert len(r.headers["x-request-id"]) == 32
    echoed = await anon_client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc-123"})
    assert echoed.headers["x-request-id"] == "trace-abc-123"
    weird = await anon_client.get("/api/v1/health", headers={"X-Request-ID": "bad id\n<script>"})
    assert weird.headers["x-request-id"] != "bad id\n<script>"  # unsafe ids are replaced


def test_json_log_lines_carry_request_id():
    token = request_id_var.set("rid-42")
    try:
        record = logging.LogRecord("procura.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        record.status = 200
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "hello world" and line["request_id"] == "rid-42" and line["status"] == 200 and line["level"] == "INFO"


# ---------- error handling ----------

async def test_unexpected_errors_do_not_leak_internals(client: AsyncClient):
    from httpx import ASGITransport

    def boom():
        raise RuntimeError("secret connection string postgres://user:pw@db")

    app.dependency_overrides[get_paypal] = boom
    try:
        # raise_app_exceptions=False: behave like a real server instead of re-raising into the test
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
                               headers=dict(client.headers)) as c:
            r = await c.get("/api/v1/payments/config")
    finally:
        app.dependency_overrides.pop(get_paypal, None)
    assert r.status_code == 500 and r.json()["detail"] == "Internal server error"
    assert "postgres" not in r.text and r.json()["request_id"] == r.headers["x-request-id"]


async def test_database_down_returns_503(anon_client: AsyncClient):
    async def down():
        raise ConnectionRefusedError("db unreachable")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = down
    try:
        r = await anon_client.post("/api/v1/auth/login", json={"email": "a@b.c", "password": "x"})
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]


# ---------- append-only history ----------

async def test_audit_and_history_are_append_only(db, request_id):
    entry = AuditLog(actor_type="system", actor_name="test", action="test.append_only", entity_type="test")
    db.add(entry)
    await db.commit()
    audit_id = entry.id  # plain ids: rollbacks expire ORM objects
    for sql in ("UPDATE audit_logs SET action = 'tampered' WHERE id = :id", "DELETE FROM audit_logs WHERE id = :id"):
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text(sql), {"id": audit_id})
        await db.rollback()
    db.add(WorkflowStateHistory(request_id=request_id, to_state="CREATED"))
    await db.commit()
    hist_id = await db.scalar(select(WorkflowStateHistory.id).where(WorkflowStateHistory.request_id == request_id).limit(1))
    for sql in ("UPDATE workflow_state_history SET to_state = 'COMPLETED' WHERE id = :id", "DELETE FROM workflow_state_history WHERE id = :id"):
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text(sql), {"id": hist_id})
        await db.rollback()


# ---------- insight endpoints ----------

@pytest.fixture
def mock_llm(monkeypatch):
    gw = LLMGateway([MockProvider(test_settings())], settings=test_settings(), recorder=None)
    monkeypatch.setattr(executor, "pipeline_factory", lambda: ProcurementPipeline(runner=AgentRunner(gateway=gw)))


async def run_to_approval(client: AsyncClient) -> tuple[str, str]:
    rid = (await client.post("/api/v1/procurement/requests", json={"raw_text": LAPTOPS})).json()["id"]
    assert (await client.post(f"/api/v1/procurement/requests/{rid}/execute")).status_code == 202
    await executor.wait(uuid.UUID(rid))
    status = (await client.get(f"/api/v1/procurement/requests/{rid}/status")).json()
    assert status["status"] == "AWAITING_APPROVAL", status["last_reason"]
    return rid, status["purchase_order_id"]


async def test_mock_llm_runs_the_real_pipeline(client: AsyncClient, mock_llm):
    rid, _ = await run_to_approval(client)
    offers = (await client.get(f"/api/v1/procurement/requests/{rid}/offers")).json()
    assert offers["requirement"]["items"][0] == {"product_name": "laptop", "quantity": 10, "specifications": {"ram_gb_min": 16}, "category": "laptops"}
    assert offers["recommended"]["vendor_name"] == "Fabrikam Office" and offers["purchase_order"]["total"] == "11890.00"
    assert {p["sku"] for p in offers["items"][0]["products"]} == {"LAP-PRO-14", "LAP-ULTRA-16"}  # 8 GB model excluded by spec


async def test_mock_paypal_full_journey_and_insights(client: AsyncClient, mock_llm, monkeypatch, approver):
    from app.core.config import get_settings

    monkeypatch.setattr(paypal_module, "_mock", None)
    monkeypatch.setattr(get_settings(), "paypal_mode", "mock")
    rid, po_id = await run_to_approval(client)
    assert (await client.post(f"/api/v1/purchase-orders/{po_id}/approve", headers=approver)).status_code == 200

    cfg = (await client.get("/api/v1/payments/config")).json()
    assert cfg["mode"] == "mock" and cfg["sdk_url"] is None
    order = (await client.post("/api/v1/payments/create-order", json={"purchase_order_id": po_id})).json()
    assert (await client.post("/api/v1/payments/mock/approve", json={"paypal_order_id": order["orderId"]}, headers=approver)).status_code == 404
    assert (await client.post("/api/v1/payments/mock/approve", json={"paypal_order_id": order["orderId"]})).status_code == 200
    cap = (await client.post("/api/v1/payments/capture", json={"paypal_order_id": order["orderId"]})).json()
    assert cap["status"] == "COMPLETED"
    await executor.wait(uuid.UUID(rid))
    assert (await client.get(f"/api/v1/procurement/requests/{rid}/status")).json()["status"] == "COMPLETED"

    # Payment Center / Approval Center / Audit views
    pays = (await client.get("/api/v1/payments")).json()
    mine = next(p for p in pays if p["request_id"] == rid)
    assert mine["receipt"]["amount"] == "11890.00" and mine["po_number"].startswith("PO-")
    approvals = (await client.get("/api/v1/approvals", headers=approver)).json()
    assert approvals[0]["decision"] == "APPROVED" and approvals[0]["approver_name"] == "Avery Approver"
    hist = (await client.get(f"/api/v1/procurement/requests/{rid}/history")).json()
    assert [h["to_state"] for h in hist][-3:] == ["PAYMENT_COMPLETED", "RECONCILING", "COMPLETED"]
    assert hist[0]["actor"] == "Riley Requester" and any(h["actor"] == "finance agent" for h in hist)
    runs = (await client.get("/api/v1/agents/runs", params={"agent": "reconciliation"})).json()
    assert runs[0]["status"] == "SUCCEEDED" and runs[0]["tool_calls"] >= 3
    intel = (await client.get("/api/v1/intelligence/summary", headers=approver)).json()
    assert {a["agent"] for a in intel["agents"]} >= {"intent", "research", "evaluation", "procurement", "finance", "reconciliation"}
    assert any(t["tool"] == "create_purchase_order" for t in intel["tools"])
    audit = (await client.get("/api/v1/audit/logs", params={"action": "payment."}, headers=approver)).json()
    assert {a["action"] for a in audit} >= {"payment.order_created", "payment.completed"}


async def test_mock_modes_are_refused_in_production(client: AsyncClient, monkeypatch):
    from app.core.config import get_settings

    from app.core import security

    s = get_settings()
    monkeypatch.setattr(s, "jwt_secret", security._secret())  # production refuses to run without an explicit secret
    monkeypatch.setattr(s, "paypal_mode", "mock")
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(paypal_module, "_mock", None)
    assert paypal_module.get_client() is paypal_module.paypal  # the real client, not the simulator
    assert not MockProvider(test_settings(app_env="production")).configured
    r = await client.post("/api/v1/payments/mock/approve", json={"paypal_order_id": "X"})
    assert r.status_code == 404
    demo = (await client.get("/api/v1/auth/demo-accounts")).json()
    assert demo == {"enabled": False, "accounts": []}


def test_production_requires_jwt_secret(monkeypatch):
    from app.core import security
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "jwt_secret", "")
    with pytest.raises(RuntimeError, match="JWT_SECRET must be set"):
        security.create_access_token(uuid.uuid4(), "admin")
