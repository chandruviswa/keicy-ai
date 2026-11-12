"""Idempotent seed: inserts missing rows by natural key, never duplicates or overwrites."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, User, Vendor, VendorOffer
from app.core.security import hash_password
from seed.data import DEMO_PASSWORD, OFFERS, PRODUCTS, USERS, VENDORS


async def seed_all(db: AsyncSession, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    added = {"users": 0, "vendors": 0, "products": 0, "offers": 0}

    existing_users = {u.email: u for u in await db.scalars(select(User))}
    for email, name, role in USERS:
        if email not in existing_users:
            db.add(User(email=email, full_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD)))
            added["users"] += 1
        elif existing_users[email].password_hash is None:  # users seeded before auth existed
            existing_users[email].password_hash = hash_password(DEMO_PASSWORD)

    vendors = {v.name: v for v in await db.scalars(select(Vendor))}
    for name, email, country, rating in VENDORS:
        if name not in vendors:
            vendors[name] = Vendor(name=name, contact_email=email, country=country, rating=rating)
            db.add(vendors[name])
            added["vendors"] += 1

    products = {p.sku: p for p in await db.scalars(select(Product))}
    for sku, name, category, desc, unit, specs in PRODUCTS:
        if sku not in products:
            products[sku] = Product(sku=sku, name=name, category=category, description=desc, unit=unit, specs=specs)
            db.add(products[sku])
            added["products"] += 1

    await db.flush()  # assign IDs to new vendors/products

    existing_offers = set((await db.execute(select(VendorOffer.vendor_id, VendorOffer.product_id))).all())
    for vname, sku, price, qty, moq, lead, valid_days, specs in OFFERS:
        v, p = vendors[vname], products[sku]
        if (v.id, p.id) in existing_offers:
            continue
        db.add(
            VendorOffer(
                vendor_id=v.id,
                product_id=p.id,
                unit_price=price,
                currency="USD",
                available_quantity=qty,
                min_order_quantity=moq,
                lead_time_days=lead,
                valid_until=today + timedelta(days=valid_days),
                specs=specs,
            )
        )
        added["offers"] += 1

    await db.commit()
    return added
