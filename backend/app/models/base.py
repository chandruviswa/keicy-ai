import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

# Money is always NUMERIC — never float. 14 digits, 2 decimals = up to 999,999,999,999.99
Money = Numeric(14, 2, asdecimal=True)
Currency = String(3)
JSON = JSONB


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CreatedAt:
    """For append-only tables (logs, history) that are never updated."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


def enum_check(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    """CHECK constraint limiting a String column to an enum's values (avoids native PG enums)."""
    values = ", ".join(f"'{e.value}'" for e in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_valid")


def currency_check(column: str = "currency") -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^[A-Z]{{3}}$'", name=f"{column}_iso")
