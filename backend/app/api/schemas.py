"""API schemas. Money is Decimal and serialises as a string (e.g. "1149.00") — never a float."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: str


class VendorOut(ORM):
    id: uuid.UUID
    name: str
    contact_email: str
    country: str
    rating: Decimal
    is_active: bool


class OfferOut(ORM):
    id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    product_id: uuid.UUID
    unit_price: Decimal
    currency: str
    available_quantity: int
    min_order_quantity: int
    lead_time_days: int
    valid_until: date
    is_expired: bool
    specs: dict[str, Any]
    is_active: bool = True


class ProductOut(ORM):
    id: uuid.UUID
    sku: str
    name: str
    category: str
    description: str
    unit: str
    specs: dict[str, Any]
    offers: list[OfferOut] = []


class VendorDetailOut(VendorOut):
    offers: list[OfferOut] = []


class VendorIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=200)
    contact_email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2, e.g. US")
    rating: Decimal = Field(Decimal("4.00"), ge=0, le=5, max_digits=3, decimal_places=2)


class VendorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


class ProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sku: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(min_length=2, max_length=64)
    description: str = Field("", max_length=2000)
    unit: str = Field("each", min_length=1, max_length=20)
    specs: dict[str, Any] = Field(default_factory=dict, description="Comparable attributes, e.g. {\"ram_gb\": 16}")


class OfferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_id: uuid.UUID
    product_id: uuid.UUID
    unit_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    available_quantity: int = Field(ge=0)
    min_order_quantity: int = Field(1, ge=1)
    lead_time_days: int = Field(ge=0, le=365)
    valid_until: date
    specs: dict[str, Any] = Field(default_factory=dict, description="Vendor-specific variant of the product specs")


class OfferPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool | None = None
    unit_price: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    available_quantity: int | None = Field(None, ge=0)
    valid_until: date | None = None


class StarterVendor(BaseModel):
    name: str
    contact_email: str
    country: str
    rating: Decimal
    offers: int
    imported: bool


class StarterProduct(BaseModel):
    sku: str
    name: str
    category: str
    description: str
    offers: int
    imported: bool


class StarterCatalog(BaseModel):
    vendors: list[StarterVendor]
    products: list[StarterProduct]


class StarterImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendors: list[str] | None = Field(None, description="Vendor names to import; omit for all")
    skus: list[str] | None = Field(None, description="Product SKUs to import; omit for all")


class ProcurementRequestIn(BaseModel):
    raw_text: str = Field(min_length=5, max_length=5000, description="Natural-language request")
    title: str | None = Field(None, max_length=200)
    budget: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    deadline: date | None = None


class ProcurementRequestOut(ORM):
    id: uuid.UUID
    requester_id: uuid.UUID
    raw_text: str
    title: str | None
    status: str
    requirements: dict[str, Any] | None
    budget: Decimal | None
    currency: str
    deadline: date | None
    created_at: datetime
    updated_at: datetime


class ProcurementRequestUpdate(BaseModel):
    """Edit a request before (re-)running it — e.g. after the Intent Agent asked for clarification."""

    raw_text: str | None = Field(None, min_length=5, max_length=5000)
    title: str | None = Field(None, max_length=200)
    budget: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    deadline: date | None = None


class AgentRunSummary(ORM):
    id: uuid.UUID
    agent_name: str
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class AgentRunOut(AgentRunSummary):
    request_id: uuid.UUID
    input: dict[str, Any] | None
    output: dict[str, Any] | None


class ToolLogOut(ORM):
    id: uuid.UUID
    tool_name: str
    is_write: bool
    status: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    duration_ms: int | None
    created_at: datetime


class ModelExecutionOut(ORM):
    id: uuid.UUID
    provider: str
    model: str
    purpose: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    attempt: int
    fallback_used: bool
    success: bool
    error_type: str | None
    created_at: datetime


class AgentRunLogsOut(BaseModel):
    run: AgentRunSummary
    tool_calls: list[ToolLogOut]
    model_calls: list[ModelExecutionOut]


class WorkflowStatusOut(BaseModel):
    id: uuid.UUID
    status: str
    is_running: bool
    allowed_next: list[str]
    last_reason: str | None
    clarification_questions: list[str]
    purchase_order_id: uuid.UUID | None
    runs: list[AgentRunSummary]


class POItemOut(ORM):
    id: uuid.UUID
    offer_id: uuid.UUID
    product_id: uuid.UUID
    description: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class ApprovalOut(ORM):
    id: uuid.UUID
    approver_id: uuid.UUID
    decision: str
    reason: str | None
    decided_at: datetime


class PurchaseOrderOut(ORM):
    id: uuid.UUID
    po_number: str
    request_id: uuid.UUID
    request_status: str | None = None
    vendor_id: uuid.UUID
    vendor_name: str | None = None
    status: str
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    notes: str | None
    created_at: datetime
    items: list[POItemOut] = []
    approvals: list[ApprovalOut] = []


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class ApproveIn(BaseModel):
    note: str | None = Field(None, max_length=2000)
