"""WebSocket: live workflow events for one request.

  ws://host/api/v1/ws/workflows/{request_id}?token=<access token>&after=<seq>

On connect the server sends {"type": "snapshot", ...current status...}, then every stored event with
seq > after (so a reconnecting client passes the last seq it saw and misses nothing), then new events
as they are committed. Events are always persisted before they are sent.
"""

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.agents.pipeline import executor
from app.api.deps import can_view, user_from_token
from app.db.session import SessionLocal
from app.models import ProcurementRequest
from app.workflow import allowed_next
from app.workflow.events import bus, event_dict, events_after

router = APIRouter(tags=["realtime"])

POLL_S = 2.0


@router.websocket("/ws/workflows/{request_id}")
async def workflow_events(websocket: WebSocket, request_id: uuid.UUID, after: int = 0, token: str | None = None):
    await websocket.accept()
    async with SessionLocal() as db:
        # Browsers can't set headers on WebSockets, so the access token comes as ?token=…
        viewer = await user_from_token(db, token)
        if viewer is None:
            await websocket.close(code=4401, reason="Sign in required")
            return
        pr = await db.get(ProcurementRequest, request_id)
        if not viewer or not pr or not can_view(viewer, pr):
            await websocket.close(code=4404, reason="Procurement request not found")
            return

    sub = bus.subscribe(request_id)
    receiver = asyncio.create_task(_drain(websocket))  # notices client disconnects
    last = after
    try:
        async with SessionLocal() as db:
            status = await db.scalar(select(ProcurementRequest.status).where(ProcurementRequest.id == request_id))
        await websocket.send_json({"type": "snapshot", "request_id": str(request_id), "status": status,
                                   "allowed_next": sorted(allowed_next(status)), "is_running": executor.is_running(request_id)})
        while not receiver.done():
            async with SessionLocal() as db:
                rows = await events_after(db, request_id, last)
            for e in rows:
                await websocket.send_json({"type": "event", **event_dict(e)})
                last = e.seq
            waiter = asyncio.create_task(sub.wait(POLL_S))
            await asyncio.wait({waiter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if not waiter.done():
                waiter.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sub.close()
        receiver.cancel()
        with contextlib.suppress(Exception):
            await websocket.close()


async def _drain(websocket: WebSocket) -> None:
    """Read (and ignore) client messages until the socket closes."""
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError):
        return
