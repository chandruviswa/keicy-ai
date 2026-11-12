"""In-process notification when workflow events are committed.

Events are always written to the database first. After a commit that contained WorkflowEvent rows,
subscribers for those requests are woken up and read the new rows from the DB. A subscriber also
polls on a short timeout, so a missed wake-up (e.g. another worker process) only adds latency.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import WorkflowEvent


class Subscription:
    def __init__(self, bus: EventBus, request_id: uuid.UUID) -> None:
        self.bus, self.request_id = bus, request_id
        self.loop = asyncio.get_running_loop()
        self.flag = asyncio.Event()

    def notify(self) -> None:
        # Commits can happen on another thread's loop (e.g. test clients) — hop to ours safely.
        self.loop.call_soon_threadsafe(self.flag.set)

    async def wait(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self.flag.wait(), timeout)
            return True
        except TimeoutError:
            return False
        finally:
            self.flag.clear()

    def close(self) -> None:
        self.bus._subs[self.request_id].discard(self)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[uuid.UUID, set[Subscription]] = defaultdict(set)

    def subscribe(self, request_id: uuid.UUID) -> Subscription:
        sub = Subscription(self, request_id)
        self._subs[request_id].add(sub)
        return sub

    def publish(self, request_id: uuid.UUID) -> None:
        for sub in list(self._subs.get(request_id, ())):
            try:
                sub.notify()
            except RuntimeError:  # subscriber's loop already closed
                sub.close()


bus = EventBus()


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _ctx) -> None:
    ids = {obj.request_id for obj in session.new if isinstance(obj, WorkflowEvent)}
    if ids:
        session.info.setdefault("_event_request_ids", set()).update(ids)


@event.listens_for(Session, "after_commit")
def _publish(session: Session) -> None:
    for rid in session.info.pop("_event_request_ids", ()):
        bus.publish(rid)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("_event_request_ids", None)


def event_dict(e: WorkflowEvent) -> dict[str, Any]:
    return {"seq": e.seq, "event_type": e.event_type, "payload": e.payload, "created_at": e.created_at.isoformat()}


async def events_after(db: AsyncSession, request_id: uuid.UUID, after_seq: int = 0, limit: int = 500) -> list[WorkflowEvent]:
    return list((await db.scalars(
        select(WorkflowEvent).where(WorkflowEvent.request_id == request_id, WorkflowEvent.seq > after_seq)
        .order_by(WorkflowEvent.seq).limit(limit)
    )).all())


async def emit(db: AsyncSession, request_id: uuid.UUID, event_type: str, payload: dict[str, Any] | None = None) -> None:
    db.add(WorkflowEvent(request_id=request_id, event_type=event_type, payload=payload or {}))
