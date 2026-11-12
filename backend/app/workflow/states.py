"""The procurement workflow graph. This table is the single source of truth for what may happen next."""

from app.models.enums import WorkflowState as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED: frozenset({S.ANALYZING, S.CANCELLED, S.FAILED}),
    S.ANALYZING: frozenset({S.RESEARCHING, S.REJECTED, S.FAILED, S.CANCELLED}),
    S.RESEARCHING: frozenset({S.EVALUATING, S.REJECTED, S.FAILED, S.CANCELLED}),
    S.EVALUATING: frozenset({S.ORDER_DRAFTED, S.REJECTED, S.FAILED, S.CANCELLED}),
    S.ORDER_DRAFTED: frozenset({S.FINANCE_VALIDATING, S.FAILED, S.CANCELLED}),
    S.FINANCE_VALIDATING: frozenset({S.AWAITING_APPROVAL, S.REJECTED, S.FAILED, S.CANCELLED}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.CANCELLED}),
    S.APPROVED: frozenset({S.PAYMENT_PENDING, S.CANCELLED}),
    S.PAYMENT_PENDING: frozenset({S.PAYMENT_PROCESSING, S.PAYMENT_FAILED, S.CANCELLED}),
    # Once PayPal is processing, the only exits are its verified outcomes — no cancelling mid-capture.
    S.PAYMENT_PROCESSING: frozenset({S.PAYMENT_COMPLETED, S.PAYMENT_FAILED}),
    S.PAYMENT_COMPLETED: frozenset({S.RECONCILING}),
    S.RECONCILING: frozenset({S.COMPLETED, S.FAILED}),
    S.PAYMENT_FAILED: frozenset({S.PAYMENT_PENDING, S.CANCELLED}),  # buyer may retry checkout
    S.FAILED: frozenset({S.ANALYZING, S.CANCELLED}),  # re-run the agents from the start
    S.COMPLETED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL: frozenset[S] = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)

# States in which agents are working. After a crash a request stuck here must be resumed or failed.
AGENT_ACTIVE: frozenset[S] = frozenset(
    {S.ANALYZING, S.RESEARCHING, S.EVALUATING, S.ORDER_DRAFTED, S.FINANCE_VALIDATING, S.RECONCILING}
)

# Entering any of these requires a recorded APPROVED decision on the request's purchase order.
REQUIRES_APPROVAL: frozenset[S] = frozenset(
    {S.APPROVED, S.PAYMENT_PENDING, S.PAYMENT_PROCESSING, S.PAYMENT_COMPLETED, S.RECONCILING, S.COMPLETED}
)


def allowed_next(state: S | str) -> frozenset[S]:
    return TRANSITIONS[S(state)]


def can_transition(from_state: S | str, to_state: S | str) -> bool:
    return S(to_state) in TRANSITIONS[S(from_state)]


assert set(TRANSITIONS) == set(S), "every WorkflowState needs an entry in TRANSITIONS"
