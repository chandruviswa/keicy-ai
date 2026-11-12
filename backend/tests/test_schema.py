"""Schema, constraint and relationship tests (Phase 2.4)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    Approval, AuditLog, Payment, PayPalCapture, PayPalOrder, Product, ProcurementRequest,
    PurchaseOrder, PurchaseOrderItem, User, Vendor, VendorOffer,
)
from seed.data import DEMO_USER_EMAIL
from seed.catalog import OFFERS, PRODUCTS, VENDORS, load_catalog
from seed.seed import seed_all

EXPECTED_TABLES = {
    "users", "vendors", "products", "vendor_offers", "procurement_requests", "purchase_orders",
    "purchase_order_items", "agent_runs", "agent_tool_logs", "workflow_events", "workflow_state_history",
    "model_executions", "approvals", "payments", "paypal_orders", "paypal_captures", "audit_logs",
}


def uid() -> str:
    return uuid.uuid4().hex[:8]


async def make_vendor(db, **kw) -> Vendor:
    v = Vendor(name=f"Vendor {uid()}", contact_email="x@example.com", country="US", **kw)
    db.add(v)
    await db.flush()
    return v


async def make_product(db) -> Product:
    p = Product(sku=f"SKU-{uid()}", name="Widget", category="test")
    db.add(p)
    await db.flush()
    return p


async def make_offer(db, vendor, product, **kw) -> VendorOffer:
    o = VendorOffer(
        vendor_id=vendor.id, product_id=product.id, unit_price=kw.pop("unit_price", Decimal("10.00")),
        available_quantity=100, lead_time_days=3, valid_until=date.today() + timedelta(days=30), **kw,
    )
    db.add(o)
    await db.flush()
    return o


async def make_po(db) -> tuple[PurchaseOrder, VendorOffer]:
    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    v, p = await make_vendor(db), await make_product(db)
    offer = await make_offer(db, v, p)
    pr = ProcurementRequest(requester_id=user.id, raw_text="need widgets")
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(po_number=f"PO-{uid()}", request_id=pr.id, vendor_id=v.id, currency="USD",
                       subtotal=Decimal("20.00"), tax_amount=Decimal("0.00"), total=Decimal("20.00"))
    db.add(po)
    await db.flush()
    db.add(PurchaseOrderItem(purchase_order_id=po.id, offer_id=offer.id, product_id=p.id, description="Widget",
                             quantity=2, unit_price=Decimal("10.00"), line_total=Decimal("20.00")))
    await db.flush()
    return po, offer


async def expect_integrity_error(db, obj=None, sql=None):
    with pytest.raises(IntegrityError):
        if obj is not None:
            db.add(obj)
            await db.flush()
        else:
            await db.execute(text(sql))
    await db.rollback()


# ---------- migrations & seed ----------

async def test_all_tables_exist(db):
    rows = await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    assert EXPECTED_TABLES <= {r[0] for r in rows}


async def test_column_types(db):
    rows = (await db.execute(text(
        "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale "
        "FROM information_schema.columns WHERE table_schema = 'public'"
    ))).all()
    cols = {(r[0], r[1]): r for r in rows}
    for table in EXPECTED_TABLES:
        assert cols[(table, "id")][2] == "uuid", table
        assert cols[(table, "created_at")][2] == "timestamp with time zone", table
    for table, col in [("vendor_offers", "unit_price"), ("purchase_orders", "total"), ("payments", "amount"),
                       ("purchase_order_items", "line_total"), ("paypal_captures", "amount")]:
        assert cols[(table, col)][2:] == ("numeric", 14, 2), (table, col)
    assert cols[("products", "specs")][2] == "jsonb"


async def test_seed_counts_and_idempotency(db):
    assert await seed_all(db) == {"users": 0}
    assert await load_catalog(db) == {"vendors": 0, "products": 0, "offers": 0}
    assert len(VENDORS) >= 4 and 10 <= len(PRODUCTS) <= 15
    for sku, *_ in PRODUCTS:
        assert sum(1 for o in OFFERS if o[1] == sku) >= 2, f"{sku} needs multiple offers"
    seeded_offers = await db.scalar(
        select(func.count()).select_from(VendorOffer).join(Product).where(Product.sku.in_([p[0] for p in PRODUCTS]))
    )
    assert seeded_offers == len(OFFERS)


async def test_seed_has_expired_and_out_of_stock_offers(db):
    assert await db.scalar(select(func.count()).select_from(VendorOffer).where(VendorOffer.valid_until < date.today())) >= 1
    assert await db.scalar(select(func.count()).select_from(VendorOffer).where(VendorOffer.available_quantity == 0)) >= 1


# ---------- constraints ----------

async def test_numeric_precision_round_trip(db):
    v, p = await make_vendor(db), await make_product(db)
    offer_id = (await make_offer(db, v, p, unit_price=Decimal("1234.57"))).id
    await db.commit()
    db.expire_all()
    stored = await db.scalar(select(VendorOffer.unit_price).where(VendorOffer.id == offer_id))
    assert stored == Decimal("1234.57") and isinstance(stored, Decimal)


async def test_unique_constraints(db):
    v = await make_vendor(db)
    await db.commit()
    await expect_integrity_error(db, Vendor(name=v.name, contact_email="y@example.com", country="US"))

    p = await make_product(db)
    await db.commit()
    await expect_integrity_error(db, Product(sku=p.sku, name="dupe", category="test"))

    v, p = await make_vendor(db), await make_product(db)
    await make_offer(db, v, p)
    await db.commit()
    await expect_integrity_error(db, VendorOffer(vendor_id=v.id, product_id=p.id, unit_price=Decimal("1"),
                                                 available_quantity=1, lead_time_days=1, valid_until=date.today()))


async def test_required_fields(db):
    await expect_integrity_error(db, Product(sku=f"SKU-{uid()}", name=None, category="test"))
    await expect_integrity_error(db, Vendor(name=f"V {uid()}", contact_email=None, country="US"))


@pytest.mark.parametrize("field,value", [
    ("unit_price", Decimal("0")), ("unit_price", Decimal("-5")),
    ("available_quantity", -1), ("currency", "usd"), ("currency", "US"),
])
async def test_offer_check_constraints(db, field, value):
    v, p = await make_vendor(db), await make_product(db)
    await db.commit()
    data = dict(vendor_id=v.id, product_id=p.id, unit_price=Decimal("10"), available_quantity=1,
                lead_time_days=1, valid_until=date.today(), currency="USD")
    data[field] = value
    await expect_integrity_error(db, VendorOffer(**data))


async def test_status_must_be_a_known_state(db):
    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    await expect_integrity_error(db, ProcurementRequest(requester_id=user.id, raw_text="x", status="NOT_A_STATE"))
    await expect_integrity_error(db, User(email=f"{uid()}@x.com", full_name="X", role="superuser"))


async def test_purchase_order_totals_must_add_up(db):
    po_id = (await make_po(db))[0].id  # rollbacks expire ORM objects, so keep plain IDs
    await db.commit()
    await expect_integrity_error(db, sql=f"UPDATE purchase_orders SET total = 999 WHERE id = '{po_id}'")
    await expect_integrity_error(db, sql=f"UPDATE purchase_order_items SET line_total = 1 WHERE purchase_order_id = '{po_id}'")


async def test_rejection_requires_reason(db):
    po, _ = await make_po(db)
    approver = await db.scalar(select(User).where(User.role == "approver"))
    await db.commit()
    await expect_integrity_error(db, Approval(purchase_order_id=po.id, approver_id=approver.id, decision="REJECTED"))


# ---------- relationships, delete & update behaviour ----------

async def test_relationships_load(db):
    from sqlalchemy.orm import selectinload

    po, offer = await make_po(db)
    await db.commit()
    po_id, offer_id, vendor_id = po.id, offer.id, offer.vendor_id
    db.expire_all()
    loaded = await db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id).options(
            selectinload(PurchaseOrder.items),
            selectinload(PurchaseOrder.vendor),
            selectinload(PurchaseOrder.request).selectinload(ProcurementRequest.purchase_orders),
        )
    )
    assert len(loaded.items) == 1 and loaded.items[0].offer_id == offer_id
    assert loaded.vendor.id == vendor_id
    assert [x.id for x in loaded.request.purchase_orders] == [loaded.id]


async def test_delete_vendor_with_offers_is_restricted(db):
    v, p = await make_vendor(db), await make_product(db)
    await make_offer(db, v, p)
    await db.commit()
    await expect_integrity_error(db, sql=f"DELETE FROM vendors WHERE id = '{v.id}'")


async def test_delete_request_with_po_is_restricted(db):
    po, _ = await make_po(db)
    await db.commit()
    await expect_integrity_error(db, sql=f"DELETE FROM procurement_requests WHERE id = '{po.request_id}'")


async def test_delete_po_cascades_to_items(db):
    po, _ = await make_po(db)
    await db.commit()
    await db.execute(text(f"DELETE FROM purchase_orders WHERE id = '{po.id}'"))
    await db.commit()
    left = await db.scalar(select(func.count()).select_from(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id))
    assert left == 0


async def test_payment_chain_and_unique_references(db):
    po, _ = await make_po(db)
    pay = Payment(purchase_order_id=po.id, amount=po.total, currency="USD", idempotency_key=f"idem-{uid()}")
    db.add(pay)
    await db.flush()
    order = PayPalOrder(payment_id=pay.id, paypal_order_id=f"ORD-{uid()}", status="CREATED", amount=po.total, currency="USD")
    db.add(order)
    await db.flush()
    cap = PayPalCapture(paypal_order_ref_id=order.id, capture_id=f"CAP-{uid()}", status="COMPLETED", amount=po.total, currency="USD")
    db.add(cap)
    await db.commit()
    po_id, total, order_id, pay_id, key, cap_id = po.id, po.total, order.id, pay.id, pay.idempotency_key, cap.capture_id

    await expect_integrity_error(db, Payment(purchase_order_id=po_id, amount=total, currency="USD", idempotency_key=key))
    await expect_integrity_error(db, PayPalCapture(paypal_order_ref_id=order_id, capture_id=cap_id, status="COMPLETED", amount=total, currency="USD"))
    await expect_integrity_error(db, sql=f"DELETE FROM payments WHERE id = '{pay_id}'")


async def test_deleting_user_keeps_audit_log(db):
    u = User(email=f"{uid()}@x.com", full_name="Temp", role="requester")
    db.add(u)
    await db.flush()
    log = AuditLog(actor_type="user", actor_user_id=u.id, action="test", entity_type="user", entity_id=u.id)
    db.add(log)
    await db.commit()
    user_id, log_id = u.id, log.id
    await db.execute(text(f"DELETE FROM users WHERE id = '{user_id}'"))
    await db.commit()
    db.expire_all()
    kept = await db.get(AuditLog, log_id)
    assert kept is not None and kept.actor_user_id is None


async def test_updated_at_changes_on_update(db):
    v = await make_vendor(db)
    await db.commit()
    before = v.updated_at
    v.rating = Decimal("3.50")
    await db.commit()
    await db.refresh(v)
    assert v.updated_at > before and v.created_at == before
