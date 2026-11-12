"""Domain schemas shared by agents and tools."""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class RequirementItem(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=1_000_000)
    specifications: dict[str, Any] = Field(default_factory=dict)
    category: str | None = None


class ProcurementRequirement(BaseModel):
    """Structured form of a natural-language request (produced by the Intent Agent in Part 3)."""

    items: list[RequirementItem] = Field(min_length=1, max_length=20)
    budget: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    deadline: date | None = None
    notes: str | None = None
