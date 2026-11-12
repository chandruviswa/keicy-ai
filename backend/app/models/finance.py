"""Finance tables: approvals, payments, PayPal references, audit log."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import JSON, CreatedAt, Currency, Money, Timestamps, UUIDPk, currency_check, enum_check
from app.models.enums import ActorType, ApprovalDecision, PaymentStatus, WebhookStatus


class Approval(UUIDPk, CreatedAt, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        enum_check("decision", ApprovalDecision),
        CheckConstraint("decision = 'APPROVED' OR reason IS NOT NULL", name="rejection_needs_reason"),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    approver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Payment(UUIDPk, Timestamps, Base):
    __tablename__ = "payments"
    __table_args__ = (
        enum_check("status", PaymentStatus),
        CheckConstraint("amount > 0", name="amount_positive"),
        currency_check(),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(Currency, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PaymentStatus.CREATED, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="paypal")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # checkout attempt number for this PO
    failure_reason: Mapped[str | None] = mapped_column(Text)
    receipt: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # written by the Reconciliation Agent
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    paypal_orders: Mapped[list["PayPalOrder"]] = relationship(back_populates="payment")


class PayPalOrder(UUIDPk, Timestamps, Base):
    __tablename__ = "paypal_orders"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"), currency_check())

    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False, index=True)
    paypal_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # PayPal's own status string
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(Currency, nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    payment: Mapped[Payment] = relationship(back_populates="paypal_orders")
    captures: Mapped[list["PayPalCapture"]] = relationship(back_populates="order")


class PayPalCapture(UUIDPk, Timestamps, Base):
    __tablename__ = "paypal_captures"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"), currency_check())

    paypal_order_ref_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paypal_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    capture_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(Currency, nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    order: Mapped[PayPalOrder] = relationship(back_populates="captures")


class WebhookEvent(UUIDPk, CreatedAt, Base):
    """Every PayPal webhook delivery, verified or not. event_id is UNIQUE so a redelivery is never processed twice."""

    __tablename__ = "webhook_events"
    __table_args__ = (enum_check("status", WebhookStatus),)

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="paypal")
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), index=True)  # e.g. the capture id
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    verification: Mapped[str | None] = mapped_column(String(32))  # PayPal's verification_status
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPk, CreatedAt, Base):
    """Append-only. Nothing in the app updates or deletes these rows."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        enum_check("actor_type", ActorType),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(100))  # agent name or "system"
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
