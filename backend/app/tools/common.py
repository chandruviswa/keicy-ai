from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.agents.core.tools import ToolRegistry

CENT = Decimal("0.01")
SUPPORTED_CURRENCIES = frozenset({"USD", "EUR", "GBP"})

# The single registry every agent uses.
registry = ToolRegistry()


def money(value: Decimal | int | str) -> Decimal:
    """All money maths is Decimal, rounded half-up to cents. Never float."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def source(table: str, row_id) -> dict[str, str]:
    """Reference to the database row a fact came from, so agents can't present invented data."""
    return {"table": table, "id": str(row_id)}


def offer_issues(offer, quantity: int, today: date | None = None) -> list[str]:
    today = today or date.today()
    issues = []
    if not offer.is_active:
        issues.append("offer inactive")
    if offer.valid_until < today:
        issues.append(f"offer expired on {offer.valid_until}")
    if offer.available_quantity < quantity:
        issues.append(f"only {offer.available_quantity} in stock")
    if quantity < offer.min_order_quantity:
        issues.append(f"minimum order is {offer.min_order_quantity}")
    if not offer.vendor.is_active:
        issues.append("vendor inactive")
    return issues
