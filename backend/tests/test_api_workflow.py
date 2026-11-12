"""Part 3 APIs: execute → status/offers/runs → approve/reject, roles, editing, dashboard, WebSocket."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from starlette.testclient import TestClient

from app.agents.core.runner import AgentRunner
from app.agents.pipeline import ProcurementPipeline, executor
from app.ai.gateway import LLMGateway
from app.main import app
from app.models import ProcurementRequest
from app.models.enums import WorkflowState as S
from app.workflow import Actor, transition
from seed.data import DEMO_USER_EMAIL
from tests.conftest import token_for
from tests.fakes import RoutingProvider, test_settings
from tests.test_pipeline import laptops, routes



@pytest.fixture
def llm(monkeypatch):
    """Route the API's background executor through a scripted LLM."""
    provider = RoutingProvider(routes())
    gw = LLMGateway([provider], settings=test_settings(), recorder=None)
    monkeypatch.setattr(executor, "pipeline_factory", lambda: ProcurementPipeline(runner=AgentRunner(gateway=gw)))
    return provider


async def create(client: AsyncClient, **body) -> str:
    r = await client.post("/api/v1/procurement/requests", json={"raw_text": "10 laptops with 16GB RAM, under $15,000 by Dec 1", **body})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def execute(client: AsyncClient, rid: str) -> dict:
    r = await client.post(f"/api/v1/procurement/requests/{rid}/execute")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "ANALYZING"
    await executor.wait(uuid.UUID(rid))
    return (await client.get(f"/api/v1/procurement/requests/{rid}/status")).json()


async def test_execute_to_approval_and_approve(client: AsyncClient, llm, approver):
    rid = await create(client)
    status = await execute(client, rid)
    assert status["status"] == "AWAITING_APPROVAL" and not status["is_running"]
    assert status["allowed_next"] == ["APPROVED", "CANCELLED", "REJECTED"]
    assert [r["agent_name"] for r in status["runs"]] == ["intent", "research", "evaluation", "procurement", "finance"]
    po_id = status["purchase_order_id"]

    offers = (await client.get(f"/api/v1/procurement/requests/{rid}/offers")).json()
    assert offers["recommended"]["vendor_name"] == "Fabrikam Office" and len(offers["ranking"]) == 3
    assert offers["purchase_order"]["total"] == "11890.00" and offers["finance_report"]["total"] == "11890.00"
    assert offers["items"][0]["quotes"] and offers["po_summary"].startswith("PO-")

    run_id = status["runs"][1]["id"]
    logs = (await client.get(f"/api/v1/agents/runs/{run_id}/logs")).json()
    assert {t["tool_name"] for t in logs["tool_calls"]} >= {"search_catalog", "get_vendor_quotes"}
    assert (await client.get(f"/api/v1/agents/runs/{run_id}")).json()["agent_name"] == "research"

    # The requester cannot approve; an approver can, exactly once.
    assert (await client.post(f"/api/v1/purchase-orders/{po_id}/approve")).status_code == 403
    r = await client.post(f"/api/v1/purchase-orders/{po_id}/approve", headers=approver, json={"note": "within budget"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and r.json()["request_status"] == "APPROVED"
    assert r.json()["approvals"][0]["decision"] == "APPROVED"
    assert (await client.post(f"/api/v1/purchase-orders/{po_id}/approve", headers=approver)).status_code == 409

    po = (await client.get(f"/api/v1/purchase-orders/{po_id}")).json()
    assert po["vendor_name"] == "Fabrikam Office" and len(po["items"]) == 1


async def test_reject_requires_reason(client: AsyncClient, llm, approver):
    rid = await create(client)
    po_id = (await execute(client, rid))["purchase_order_id"]
    assert (await client.post(f"/api/v1/purchase-orders/{po_id}/reject", headers=approver, json={})).status_code == 422
    r = await client.post(f"/api/v1/purchase-orders/{po_id}/reject", headers=approver, json={"reason": "Use the preferred supplier"})
    assert r.json()["status"] == "REJECTED" and r.json()["request_status"] == "REJECTED"


async def test_clarify_then_edit_and_rerun(client: AsyncClient, llm):
    llm.routes["Intent Agent"] = laptops(budget=None)
    rid = await create(client)
    status = await execute(client, rid)
    assert status["status"] == "FAILED"
    assert "What is the maximum budget for this purchase?" in status["clarification_questions"]

    assert (await client.patch(f"/api/v1/procurement/requests/{rid}", json={"budget": "15000.00"})).status_code == 200
    status = await execute(client, rid)
    assert status["status"] == "AWAITING_APPROVAL" and status["clarification_questions"] == []
    assert (await client.patch(f"/api/v1/procurement/requests/{rid}", json={"budget": "1.00"})).status_code == 409


async def test_execute_guards(client: AsyncClient, llm, db, approver):
    rid = await create(client)
    await execute(client, rid)
    assert (await client.post(f"/api/v1/procurement/requests/{rid}/execute")).status_code == 409  # AWAITING_APPROVAL
    assert (await client.post(f"/api/v1/procurement/requests/{uuid.uuid4()}/execute")).status_code == 404
    # An approver can see the request but not run it.
    other = await create(client)
    assert (await client.get(f"/api/v1/procurement/requests/{other}", headers=approver)).status_code == 200
    assert (await client.post(f"/api/v1/procurement/requests/{other}/execute", headers=approver)).status_code == 403


async def test_cancel(client: AsyncClient):
    rid = await create(client)
    r = await client.post(f"/api/v1/procurement/requests/{rid}/cancel")
    assert r.json()["status"] == "CANCELLED" and r.json()["allowed_next"] == []
    assert (await client.post(f"/api/v1/procurement/requests/{rid}/cancel")).status_code == 409


async def test_demo_header_no_longer_switches_user(anon_client: AsyncClient):
    r = await anon_client.get("/api/v1/procurement/requests", headers={"X-Demo-User": "approver@keicy.local"})
    assert r.status_code == 401


async def test_dashboard_summary(client: AsyncClient, llm, admin):
    await execute(client, await create(client))
    s = (await client.get("/api/v1/dashboard/summary")).json()
    assert s["pending_approvals"] >= 1 and s["total_requests"] >= 1
    assert s["recent_purchase_orders"][0]["po_number"].startswith("PO-")
    assert all(a["event_type"] != "agent.tool_called" for a in s["recent_activity"])
    admin = (await client.get("/api/v1/dashboard/summary", headers=admin)).json()
    assert admin["total_requests"] >= s["total_requests"]
    assert s["scope"] == "mine" and admin["scope"] == "organisation"
    assert {st["key"] for st in s["pipeline"]} == {"intake", "agents", "approval", "payment", "completed", "attention", "closed"}
    assert sum(st["count"] for st in s["pipeline"]) == s["total_requests"]
    assert len(s["spend_by_day"]) == 30 and s["spend_by_day"][-1]["date"] == __import__("datetime").date.today().isoformat()
    assert float(s["money"]["awaiting_approval"]) > 0
    assert s["speed"]["median_minutes_to_po"] is not None
    assert s["agents"]["runs"] >= 5 and s["agents"]["success_rate"] is not None
    cat = s["catalogue"]
    assert cat["vendors"] >= 4 and cat["products"] >= 10 and cat["live_offers"] > 0 and cat["expired"] >= 1 and cat["out_of_stock"] >= 1


async def test_purchase_order_listing_scoped_to_requester(client: AsyncClient, llm, approver):
    await execute(client, await create(client))
    mine = (await client.get("/api/v1/purchase-orders")).json()
    assert mine and all(p["vendor_name"] for p in mine)
    pending = (await client.get("/api/v1/purchase-orders", params={"status": "PENDING_APPROVAL"}, headers=approver)).json()
    assert all(p["status"] == "PENDING_APPROVAL" for p in pending)


# ---------- WebSocket ----------

async def test_websocket_replays_then_streams_live(db, request_id):
    for s in (S.ANALYZING, S.RESEARCHING):
        await transition(db, request_id, s, actor=Actor.system("test"))

    tc = TestClient(app)  # no lifespan: we don't want startup recovery touching other tests' requests
    token = await token_for(DEMO_USER_EMAIL)
    with tc.websocket_connect(f"/api/v1/ws/workflows/{request_id}?token={token}") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["status"] == "RESEARCHING"
        first, second = ws.receive_json(), ws.receive_json()
        assert [first["payload"]["to"], second["payload"]["to"]] == ["ANALYZING", "RESEARCHING"]
        assert second["seq"] > first["seq"]

        await transition(db, request_id, S.CANCELLED, actor=Actor.system("test"))  # committed after connect
        live = [ws.receive_json(), ws.receive_json()]
        assert [e["event_type"] for e in live] == ["workflow.state_changed", "workflow.cancelled"]

    # Reconnect with a cursor: only events after it are sent.
    with tc.websocket_connect(f"/api/v1/ws/workflows/{request_id}?token={token}&after={second['seq']}") as ws:
        assert ws.receive_json()["status"] == "CANCELLED"
        assert ws.receive_json()["event_type"] == "workflow.state_changed"


@pytest.mark.parametrize("who,code", [("requester@keicy.local", 4404), (None, 4401), ("requester2@keicy.local", 4404)])
async def test_websocket_rejects_unknown_or_unauthorised(request_id, who, code):
    from starlette.websockets import WebSocketDisconnect

    target = uuid.uuid4() if who == "requester@keicy.local" else request_id  # requester2 may not see requester's request
    q = f"?token={await token_for(who)}" if who else ""
    with TestClient(app).websocket_connect(f"/api/v1/ws/workflows/{target}{q}") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == code
