from enum import StrEnum


class UserRole(StrEnum):
    REQUESTER = "requester"
    APPROVER = "approver"
    ADMIN = "admin"


class WorkflowState(StrEnum):
    """Procurement request lifecycle. Transition rules arrive in Part 2."""

    CREATED = "CREATED"
    ANALYZING = "ANALYZING"
    RESEARCHING = "RESEARCHING"
    EVALUATING = "EVALUATING"
    ORDER_DRAFTED = "ORDER_DRAFTED"
    FINANCE_VALIDATING = "FINANCE_VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_PROCESSING = "PAYMENT_PROCESSING"
    PAYMENT_COMPLETED = "PAYMENT_COMPLETED"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    CANCELLED = "CANCELLED"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WebhookStatus(StrEnum):
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"  # verified, but nothing to do (unknown order, already settled, irrelevant type)
    REJECTED = "REJECTED"  # signature verification failed
    FAILED = "FAILED"  # verified, but processing raised


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class ToolCallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    BLOCKED = "BLOCKED"  # agent not permitted to use this tool, or tool-call limit reached
    DUPLICATE = "DUPLICATE"  # write already done under this idempotency key; stored result returned


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
