from typing import Literal

from pydantic import BaseModel


class StepOutcome(BaseModel):
    """Every pipeline agent says what should happen next.

    continue → next workflow state · reject → REJECTED (terminal, with reason)
    clarify  → FAILED with questions; the requester edits the request and re-runs it.
    """

    outcome: Literal["continue", "reject", "clarify"] = "continue"
    reason: str | None = None
