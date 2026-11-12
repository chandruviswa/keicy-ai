"""Core business tables: users, catalogue, requests, purchase orders."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.base import JSON, Currency, Money, Timestamps, UUIDPk, currency_check, enum_check
from app.models.enums import PurchaseOrderStatus, UserRole, WorkflowState


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (enum_check("role", UserRole),)

    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.REQUESTER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # set in Part 5 (auth)


class Vendor(UUIDPk, Timestamps, Base):
    __tablename__ = "vendors"
    __table_args__ = (CheckConstraint("rating >= 0 AND rating <= 5", name="rating_range"),)

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    contact_email: Mapped[str] = mapped_column(String(254), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)  # ISO 3166-1 alpha-2
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("4.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    offers: Mapped[list["VendorOffer"]] = relationship(back_populates="vendor")


class Product(UUIDPk, Timestamps, Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="each")
    specs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    offers: Mapped[list["VendorOffer"]] = relationship(back_populates="product")


class VendorOffer(UUIDPk, Timestamps, Base):
    __tablename__ = "vendor_offers"
    __table_args__ = (
        UniqueConstraint("vendor_id", "product_id"),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
        CheckConstraint("available_quantity >= 0", name="available_quantity_nonneg"),
        CheckConstraint("min_order_quantity >= 1", name="min_order_quantity_positive"),
        CheckConstraint("lead_time_days >= 0", name="lead_time_nonneg"),
        currency_check(),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(Currency, nullable=False, default="USD")
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    min_order_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    specs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)  # vendor-specific variant
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    vendor: Mapped[Vendor] = relationship(back_populates="offers")
    product: Mapped[Product] = relationship(back_populates="offers")


class ProcurementRequest(UUIDPk, Timestamps, Base):
    __tablename__ = "procurement_requests"
    __table_args__ = (
        enum_check("status", WorkflowState),
        CheckConstraint("budget IS NULL OR budget > 0", name="budget_positive"),
        currency_check(),
    )

    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=WorkflowState.CREATED, index=True)
    requirements: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # filled by Intent Agent
    budget: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(Currency, nullable=False, default="USD")
    deadline: Mapped[date | None] = mapped_column(Date)

    requester: Mapped[User] = relationship()
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="request")


class PurchaseOrder(UUIDPk, Timestamps, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        enum_check("status", PurchaseOrderStatus),
        CheckConstraint("subtotal >= 0 AND tax_amount >= 0 AND total >= 0", name="amounts_nonneg"),
        CheckConstraint("total = subtotal + tax_amount", name="total_matches"),
        currency_check(),
    )

    po_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("procurement_requests.id", ondelete="RESTRICT"), nullable=False, index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PurchaseOrderStatus.DRAFT, index=True)
    currency: Mapped[str] = mapped_column(Currency, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0.00"))
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    request: Mapped[ProcurementRequest] = relationship(back_populates="purchase_orders")
    vendor: Mapped[Vendor] = relationship()
    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="purchase_order", cascade="all, delete-orphan", passive_deletes=True)


class PurchaseOrderItem(UUIDPk, Timestamps, Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "offer_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
        CheckConstraint("line_total = unit_price * quantity", name="line_total_matches"),
    )

    # Line items are part of the PO: deleting a PO removes its items.
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendor_offers.id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="items")
