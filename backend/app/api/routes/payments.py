"""PayPal payment endpoints. The browser never sends an amount — it only names what to pay."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import can_view, get_current_user, get_paypal, get_visible_request
from app.core.config import get_settings
from app.db.session import get_session
from app.models import Payment, PayPalCapture, PayPalOrder, ProcurementRequest, PurchaseOrder, User
from app.models.enums import UserRole
from app.payments.paypal import PayPalClient
from app.payments.service import PaymentError, PaymentService, payments_for_request

router = APIRouter(prefix="/payments", tags=["payments"])


class CreateOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an `amount` field is rejected outright, not silently ignored
    purchase_order_id: uuid.UUID


class CaptureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paypal_order_id: str = Field(min_length=1, max_length=64)


class CancelIn(BaseModel):
    paypal_order_id: str = Field(min_length=1, max_length=64)


class CaptureOut(BaseModel):
    payment_id: uuid.UUID
    status: str
    request_status: str
    capture_id: str | None
    message: str | None
    duplicate: bool
    issues: list[str]


class PayPalOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    paypal_order_id: str
    status: str
    amount: Decimal
    currency: str
    created_at: datetime


class CaptureRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    capture_id: str
    status: str
    amount: Decimal
    currency: str
    created_at: datetime


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    purchase_order_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    attempt: int
    failure_reason: str | None
    receipt: dict[str, Any] | None
    reconciled_at: datetime | None
    created_at: datetime
    paypal_orders: list[PayPalOrderOut] = []
    captures: list[CaptureRowOut] = []
    po_number: str | None = None
    request_id: uuid.UUID | None = None
    request_title: str | None = None


def _raise(e: PaymentError):
    raise HTTPException(e.status_code, e.message) from e


@router.get("/config")
async def payment_config(pp: PayPalClient = Depends(get_paypal), _: User = Depends(get_current_user)):
    """Public values the browser needs for the PayPal JS SDK v6. The secret never leaves the server."""
    s = get_settings()
    sandbox = s.paypal_mode != "live"
    return {
        "configured": pp.configured,
        "client_id": s.paypal_client_id,
        "currency": s.paypal_currency,
        "mode": "sandbox" if sandbox else "live",
        "sdk_url": "https://www.sandbox.paypal.com/web-sdk/v6/core" if sandbox else "https://www.paypal.com/web-sdk/v6/core",
    }


@router.post("/create-order")
async def create_order(body: CreateOrderIn, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user),
                       pp: PayPalClient = Depends(get_paypal)):
    try:
        o = await PaymentService(db, pp).create_order(body.purchase_order_id, user)
    except PaymentError as e:
        _raise(e)
    # `orderId` is the shape the PayPal v6 session expects from the createOrder promise.
    return {"orderId": o.paypal_order_id, "payment_id": str(o.payment_id), "status": o.status, "amount": str(o.amount),
            "currency": o.currency, "reused": o.reused}


@router.post("/capture", response_model=CaptureOut)
async def capture(body: CaptureIn, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user),
                  pp: PayPalClient = Depends(get_paypal)):
    try:
        out = await PaymentService(db, pp).capture(body.paypal_order_id, user)
    except PaymentError as e:
        _raise(e)
    code = 202 if out.status == "PROCESSING" else 200
    return JSONResponse(CaptureOut(**out.__dict__).model_dump(mode="json"), status_code=code)


@router.post("/cancel", response_model=CaptureOut)
async def cancel(body: CancelIn, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user),
                 pp: PayPalClient = Depends(get_paypal)):
    """The buyer closed or cancelled the PayPal window. Nothing was charged; they can start again."""
    try:
        out = await PaymentService(db, pp).cancel_checkout(body.paypal_order_id, user)
    except PaymentError as e:
        _raise(e)
    return CaptureOut(**out.__dict__)


@router.post("/webhook", include_in_schema=True)
async def webhook(request: Request, db: AsyncSession = Depends(get_session), pp: PayPalClient = Depends(get_paypal)):
    """PayPal → us. Verified with PayPal before anything is trusted; each event id is processed once."""
    raw = await request.body()
    code, body = await PaymentService(db, pp).handle_webhook(dict(request.headers), raw)
    return JSONResponse(body, status_code=code)


async def _payment_out(db: AsyncSession, p: Payment) -> PaymentOut:
    orders = (await db.scalars(select(PayPalOrder).where(PayPalOrder.payment_id == p.id).order_by(PayPalOrder.created_at))).all()
    caps = (await db.scalars(select(PayPalCapture).where(PayPalCapture.paypal_order_ref_id.in_([o.id for o in orders])))).all() if orders else []
    po = await db.get(PurchaseOrder, p.purchase_order_id)
    pr = await db.get(ProcurementRequest, po.request_id)
    extra = ("paypal_orders", "captures", "po_number", "request_id", "request_title")
    columns = {f: getattr(p, f) for f in PaymentOut.model_fields if f not in extra}  # plain columns only: no lazy loads
    return PaymentOut(**columns, paypal_orders=[PayPalOrderOut.model_validate(o) for o in orders],
                      captures=[CaptureRowOut.model_validate(c) for c in caps], po_number=po.po_number, request_id=pr.id, request_title=pr.title)


@router.get("", response_model=list[PaymentOut])
async def list_payments(request_id: uuid.UUID | None = None, status: str | None = None, limit: int = 100,
                        db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    """Payments for one request, or (Payment Center) all payments the user may see, newest first."""
    if request_id:
        await get_visible_request(db, request_id, user)
        return [await _payment_out(db, p) for p in await payments_for_request(db, request_id)]
    stmt = (select(Payment).join(PurchaseOrder, PurchaseOrder.id == Payment.purchase_order_id)
            .join(ProcurementRequest, ProcurementRequest.id == PurchaseOrder.request_id)
            .order_by(Payment.created_at.desc()).limit(min(max(limit, 1), 200)))
    if user.role == UserRole.REQUESTER:
        stmt = stmt.where(ProcurementRequest.requester_id == user.id)
    if status:
        stmt = stmt.where(Payment.status == status)
    return [await _payment_out(db, p) for p in (await db.scalars(stmt)).all()]


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: uuid.UUID, db: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    p = await db.get(Payment, payment_id)
    po = await db.get(PurchaseOrder, p.purchase_order_id) if p else None
    pr = await db.get(ProcurementRequest, po.request_id) if po else None
    if not p or not can_view(user, pr):
        raise HTTPException(404, "Payment not found")
    return await _payment_out(db, p)
