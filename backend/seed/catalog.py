"""Starter catalogue: real-world-style vendors, products and offers that an approver or admin can
import with one click from Vendor Intelligence (all of it, or chosen vendors/products). Nothing is
loaded automatically. Natural keys (vendor name, SKU, vendor+SKU) make importing idempotent.

Offers deliberately differ in price, stock, lead time, spec variant and expiry so the
Research and Evaluation agents have real trade-offs to reason about.
"""

from decimal import Decimal as D

VENDORS = [
    # name, contact_email, country, rating
    ("Northwind IT Supply", "sales@northwind.example", "US", D("4.60")),
    ("Contoso Devices", "orders@contoso.example", "US", D("4.20")),
    ("Fabrikam Office", "hello@fabrikam.example", "GB", D("4.40")),
    ("Adatum Cloud Services", "billing@adatum.example", "IE", D("4.70")),
    ("Tailspin Workspace", "b2b@tailspin.example", "DE", D("4.10")),
]

PRODUCTS = [
    # sku, name, category, description, unit, specs
    ("LAP-PRO-14", "ProBook 14 Laptop", "laptops", "14\" business laptop", "each",
     {"cpu": "8-core", "ram_gb": 16, "storage_gb": 512, "screen_in": 14, "weight_kg": 1.4}),
    ("LAP-ULTRA-16", "UltraBook 16 Laptop", "laptops", "16\" performance laptop for engineering", "each",
     {"cpu": "12-core", "ram_gb": 32, "storage_gb": 1024, "screen_in": 16, "weight_kg": 2.0}),
    ("LAP-ESS-13", "Essential 13 Laptop", "laptops", "Entry-level 13\" laptop", "each",
     {"cpu": "4-core", "ram_gb": 8, "storage_gb": 256, "screen_in": 13, "weight_kg": 1.2}),
    ("MON-27-4K", "27\" 4K Monitor", "monitors", "27-inch 4K IPS, USB-C 90W", "each",
     {"size_in": 27, "resolution": "3840x2160", "panel": "IPS", "usb_c_pd_w": 90}),
    ("MON-24-FHD", "24\" FHD Monitor", "monitors", "24-inch 1080p office monitor", "each",
     {"size_in": 24, "resolution": "1920x1080", "panel": "IPS"}),
    ("DOCK-USBC", "USB-C Docking Station", "peripherals", "Dual-display dock, 100W pass-through", "each",
     {"ports": ["2x HDMI", "3x USB-A", "Ethernet"], "power_w": 100}),
    ("KB-WL", "Wireless Keyboard", "peripherals", "Low-profile wireless keyboard", "each",
     {"layout": "US", "connectivity": "Bluetooth"}),
    ("MS-ERGO", "Ergonomic Mouse", "peripherals", "Vertical ergonomic mouse", "each",
     {"dpi": 4000, "connectivity": "Bluetooth"}),
    ("HS-ANC", "Noise-Cancelling Headset", "peripherals", "Wireless ANC headset with mic", "each",
     {"anc": True, "battery_h": 30}),
    ("CHAIR-ERGO", "Ergonomic Office Chair", "furniture", "Mesh chair, lumbar support", "each",
     {"adjustable_arms": True, "max_load_kg": 130}),
    ("DESK-SIT", "Sit-Stand Desk", "furniture", "Electric height-adjustable desk 160x80", "each",
     {"width_cm": 160, "depth_cm": 80, "motors": 2}),
    ("LIC-OFFICE-1Y", "Office Suite License (1 yr)", "software", "Per-user annual productivity licence", "seat",
     {"term_months": 12, "seats": 1}),
    ("LIC-SEC-1Y", "Endpoint Security License (1 yr)", "software", "Per-device annual EDR licence", "seat",
     {"term_months": 12, "devices": 1}),
]

# vendor, sku, unit_price, available_qty, min_order_qty, lead_days, valid_for_days, spec variant
# valid_for_days < 0 means the offer has already expired (agents must ignore it).
OFFERS = [
    ("Northwind IT Supply", "LAP-PRO-14", D("1149.00"), 120, 1, 5, 60, {}),
    ("Contoso Devices", "LAP-PRO-14", D("1099.00"), 40, 5, 10, 45, {"warranty_years": 1}),
    ("Fabrikam Office", "LAP-PRO-14", D("1189.00"), 300, 1, 3, 30, {"warranty_years": 3}),
    ("Northwind IT Supply", "LAP-ULTRA-16", D("2299.00"), 25, 1, 7, 60, {}),
    ("Contoso Devices", "LAP-ULTRA-16", D("2149.00"), 8, 1, 14, 30, {"ram_gb": 32, "storage_gb": 2048}),
    ("Northwind IT Supply", "LAP-ESS-13", D("649.00"), 200, 1, 4, 60, {}),
    ("Contoso Devices", "LAP-ESS-13", D("599.00"), 500, 10, 6, -5, {}),  # expired
    ("Fabrikam Office", "LAP-ESS-13", D("629.00"), 80, 1, 2, 30, {}),
    ("Northwind IT Supply", "MON-27-4K", D("429.00"), 60, 1, 5, 60, {}),
    ("Contoso Devices", "MON-27-4K", D("399.00"), 150, 2, 8, 45, {}),
    ("Fabrikam Office", "MON-27-4K", D("449.00"), 35, 1, 2, 30, {"usb_c_pd_w": 65}),
    ("Contoso Devices", "MON-24-FHD", D("159.00"), 400, 1, 5, 45, {}),
    ("Fabrikam Office", "MON-24-FHD", D("149.00"), 90, 5, 3, 30, {}),
    ("Northwind IT Supply", "DOCK-USBC", D("219.00"), 70, 1, 5, 60, {}),
    ("Contoso Devices", "DOCK-USBC", D("189.00"), 0, 1, 21, 45, {}),  # out of stock
    ("Fabrikam Office", "KB-WL", D("69.00"), 500, 1, 2, 90, {}),
    ("Northwind IT Supply", "KB-WL", D("74.00"), 200, 1, 4, 60, {"layout": "UK"}),
    ("Fabrikam Office", "MS-ERGO", D("49.00"), 300, 1, 2, 90, {}),
    ("Northwind IT Supply", "MS-ERGO", D("45.00"), 120, 10, 5, 60, {}),
    ("Fabrikam Office", "HS-ANC", D("179.00"), 60, 1, 3, 30, {}),
    ("Contoso Devices", "HS-ANC", D("165.00"), 200, 5, 7, 45, {"battery_h": 24}),
    ("Tailspin Workspace", "CHAIR-ERGO", D("389.00"), 45, 1, 14, 60, {}),
    ("Fabrikam Office", "CHAIR-ERGO", D("429.00"), 20, 1, 5, 30, {"color": "black"}),
    ("Tailspin Workspace", "DESK-SIT", D("649.00"), 30, 1, 21, 60, {}),
    ("Fabrikam Office", "DESK-SIT", D("699.00"), 12, 1, 10, 30, {"width_cm": 140}),
    ("Adatum Cloud Services", "LIC-OFFICE-1Y", D("144.00"), 10000, 5, 0, 365, {}),
    ("Northwind IT Supply", "LIC-OFFICE-1Y", D("150.00"), 1000, 1, 1, 90, {}),
    ("Adatum Cloud Services", "LIC-SEC-1Y", D("59.00"), 10000, 10, 0, 365, {}),
    ("Contoso Devices", "LIC-SEC-1Y", D("64.00"), 2000, 1, 1, 45, {}),
]


async def load_catalog(db, today=None, vendors: list[str] | None = None, skus: list[str] | None = None) -> dict[str, int]:
    """Insert missing vendors, products and offers by natural key. `vendors` / `skus` limit the import
    (None = everything); an offer is added only when both its vendor and its product are in the catalogue."""
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.models import Product, Vendor, VendorOffer

    today = today or date.today()
    want_v = set(vendors) if vendors is not None else {v[0] for v in VENDORS}
    want_p = set(skus) if skus is not None else {p[0] for p in PRODUCTS}
    added = {"vendors": 0, "products": 0, "offers": 0}
    existing_v = {v.name: v for v in await db.scalars(select(Vendor))}
    for name, email, country, rating in VENDORS:
        if name in want_v and name not in existing_v:
            existing_v[name] = Vendor(name=name, contact_email=email, country=country, rating=rating)
            db.add(existing_v[name])
            added["vendors"] += 1
    existing_p = {p.sku: p for p in await db.scalars(select(Product))}
    for sku, name, category, desc, unit, specs in PRODUCTS:
        if sku in want_p and sku not in existing_p:
            existing_p[sku] = Product(sku=sku, name=name, category=category, description=desc, unit=unit, specs=specs)
            db.add(existing_p[sku])
            added["products"] += 1
    await db.flush()
    have = set((await db.execute(select(VendorOffer.vendor_id, VendorOffer.product_id))).all())
    for vname, sku, price, qty, moq, lead, valid_days, specs in OFFERS:
        if not (vname in want_v or sku in want_p):
            continue  # neither side was chosen in this import
        v, p = existing_v.get(vname), existing_p.get(sku)
        if v is None or p is None or (v.id, p.id) in have:
            continue
        db.add(VendorOffer(vendor_id=v.id, product_id=p.id, unit_price=price, currency="USD", available_quantity=qty,
                           min_order_quantity=moq, lead_time_days=lead, valid_until=today + timedelta(days=valid_days), specs=specs))
        have.add((v.id, p.id))
        added["offers"] += 1
    await db.commit()
    return added
