"""Catalogue management: approvers and admins add vendors, products and offers; requesters can only browse."""

import uuid
from datetime import date, timedelta

from httpx import AsyncClient


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_requesters_cannot_change_the_catalogue(client: AsyncClient):
    r = await client.post("/api/v1/vendors", json={"name": "Nope Ltd", "contact_email": "a@b.co", "country": "US"})
    assert r.status_code == 403
    assert (await client.post("/api/v1/products", json={"sku": "X-1", "name": "X", "category": "misc"})).status_code == 403


async def test_admin_adds_vendor_product_and_offer(client: AsyncClient, approver):
    name = _unique("Acme Supplies")
    v = await client.post("/api/v1/vendors", headers=approver,
                          json={"name": name, "contact_email": "sales@acme.example", "country": "us", "rating": "4.5"})
    assert v.status_code == 201, v.text
    vendor = v.json()
    assert vendor["country"] == "US" and vendor["is_active"] is True
    dup = await client.post("/api/v1/vendors", headers=approver, json={"name": name.upper(), "contact_email": "x@y.co", "country": "US"})
    assert dup.status_code == 409

    sku = _unique("tab").upper()
    p = await client.post("/api/v1/products", headers=approver,
                          json={"sku": sku.lower(), "name": "Field Tablet", "category": "Tablets", "specs": {"ram_gb": 8}})
    assert p.status_code == 201, p.text
    product = p.json()
    assert product["sku"] == sku and product["category"] == "tablets" and product["offers"] == []

    body = {"vendor_id": vendor["id"], "product_id": product["id"], "unit_price": "499.00", "available_quantity": 50,
            "min_order_quantity": 1, "lead_time_days": 4, "valid_until": str(date.today() + timedelta(days=30))}
    o = await client.post("/api/v1/offers", headers=approver, json=body)
    assert o.status_code == 201, o.text
    offer = o.json()
    assert offer["vendor_name"] == name and offer["unit_price"] == "499.00" and offer["is_active"] is True
    assert (await client.post("/api/v1/offers", headers=approver, json=body)).status_code == 409

    listed = (await client.get("/api/v1/products", params={"q": sku})).json()
    assert [o["id"] for o in listed[0]["offers"]] == [offer["id"]]

    off = await client.patch(f"/api/v1/offers/{offer['id']}", headers=approver, json={"is_active": False, "unit_price": "479.00"})
    assert off.json()["is_active"] is False and off.json()["unit_price"] == "479.00"
    pause = await client.patch(f"/api/v1/vendors/{vendor['id']}", headers=approver, json={"is_active": False})
    assert pause.json()["is_active"] is False

    audit = (await client.get("/api/v1/audit/logs", params={"action": "offer."}, headers=approver)).json()
    assert {"offer.created", "offer.updated"} <= {a["action"] for a in audit}


async def test_offer_validation(client: AsyncClient, approver):
    bad = await client.post("/api/v1/offers", headers=approver, json={
        "vendor_id": str(uuid.uuid4()), "product_id": str(uuid.uuid4()), "unit_price": "0", "available_quantity": 1,
        "lead_time_days": 1, "valid_until": str(date.today())})
    assert bad.status_code == 422
    missing = await client.post("/api/v1/offers", headers=approver, json={
        "vendor_id": str(uuid.uuid4()), "product_id": str(uuid.uuid4()), "unit_price": "10", "available_quantity": 1,
        "lead_time_days": 1, "valid_until": str(date.today())})
    assert missing.status_code == 404


async def test_starter_catalogue_listing_and_import(client: AsyncClient, approver):
    assert (await client.get("/api/v1/catalog/starter")).status_code == 403  # requesters can't import
    listing = (await client.get("/api/v1/catalog/starter", headers=approver)).json()
    assert len(listing["vendors"]) >= 4 and len(listing["products"]) >= 10
    assert all(v["imported"] for v in listing["vendors"])  # the test database already holds it
    again = await client.post("/api/v1/catalog/starter/import", headers=approver, json={})
    assert again.json()["added"] == {"vendors": 0, "products": 0, "offers": 0}  # idempotent
    bad = await client.post("/api/v1/catalog/starter/import", headers=approver, json={"skus": ["NOPE"]})
    assert bad.status_code == 422


async def test_selective_starter_import(db):
    from sqlalchemy import select

    from app.models import Product, Vendor, VendorOffer
    from seed.catalog import load_catalog

    # An empty selection imports nothing; the full catalogue (loaded at session start) has its offers linked.
    assert await load_catalog(db, vendors=[], skus=[]) == {"vendors": 0, "products": 0, "offers": 0}
    v = await db.scalar(select(Vendor).where(Vendor.name == "Northwind IT Supply"))
    p = await db.scalar(select(Product).where(Product.sku == "KB-WL"))
    assert await db.scalar(select(VendorOffer.id).where(VendorOffer.vendor_id == v.id, VendorOffer.product_id == p.id))
