"""AI and workflow tables: agent runs, tool logs, events, state history, model usage."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.base import JSON, CreatedAt, Timestamps, UUIDPk, enum_check
from app.models.enums import RunStatus, ToolCallStatus, WorkflowState


class AgentRun(UUIDPk, Timestamps, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (enum_check("status", RunStatus),)

    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.RUNNING)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentToolLog(UUIDPk, CreatedAt, Base):
    __tablename__ = "agent_tool_logs"
    __table_args__ = (
        enum_check("status", ToolCallStatus),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonneg"),
    )

    agent_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # Write tools pass a key so a retried call can't repeat a business operation.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)


class WorkflowEvent(UUIDPk, CreatedAt, Base):
    """Persisted first, then pushed over WebSocket. `seq` is the client's resume cursor."""

    __tablename__ = "workflow_events"
    __table_args__ = (
        Index("ix_workflow_events_request_created", "request_id", "created_at"),
        Index("ix_workflow_events_request_seq", "request_id", "seq"),
    )

    # created_at is the transaction start time, so several events can share it; seq is strictly increasing.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False, unique=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # e.g. agent.started
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class WorkflowStateHistory(UUIDPk, CreatedAt, Base):
    __tablename__ = "workflow_state_history"
    __table_args__ = (
        enum_check("to_state", WorkflowState),
        CheckConstraint("from_state IS NULL OR from_state <> to_state", name="state_changes"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False, index=True)
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_agent: Mapped[str | None] = mapped_column(String(64))


class ModelExecution(UUIDPk, CreatedAt, Base):
    __tablename__ = "model_executions"
    __table_args__ = (CheckConstraint("latency_ms >= 0", name="latency_nonneg"),)

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # anthropic | openai
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
