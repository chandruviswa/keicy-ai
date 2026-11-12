import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_role
from app.api.schemas import (
    OfferIn, OfferOut, OfferPatch, ProductIn, ProductOut, StarterCatalog, StarterImportIn, VendorDetailOut, VendorIn,
    VendorOut, VendorPatch,
)
from app.db.session import get_session
from app.models import AuditLog, Product, User, Vendor, VendorOffer
from app.models.enums import ActorType, UserRole

# Approvers and admins manage the catalogue; requesters can only browse it.
manage_catalog = require_role(UserRole.APPROVER, UserRole.ADMIN)


def _audit(db: AsyncSession, user: User, action: str, entity_type: str, entity_id: uuid.UUID, details: dict) -> None:
    db.add(AuditLog(actor_type=ActorType.USER, actor_user_id=user.id, action=action, entity_type=entity_type,
                    entity_id=entity_id, details=details))

router = APIRouter(tags=["catalog"], dependencies=[Depends(get_current_user)])


def offer_out(o: VendorOffer) -> OfferOut:
    return OfferOut.model_validate(
        {
            **{c: getattr(o, c) for c in OfferOut.model_fields if hasattr(o, c)},
            "vendor_name": o.vendor.name,
            "is_expired": o.valid_until < date.today(),
        }
    )


def _fields(obj, schema) -> dict:
    """Plain columns only — nested offers are built separately with offer_out()."""
    return {c: getattr(obj, c) for c in schema.model_fields if c != "offers"}


@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(db: AsyncSession = Depends(get_session)):
    return (await db.scalars(select(Vendor).order_by(Vendor.name))).all()


@router.get("/vendors/{vendor_id}", response_model=VendorDetailOut)
async def get_vendor(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    v = await db.scalar(
        select(Vendor).where(Vendor.id == vendor_id).options(selectinload(Vendor.offers).selectinload(VendorOffer.vendor))
    )
    if not v:
        raise HTTPException(404, "Vendor not found")
    return VendorDetailOut(**_fields(v, VendorDetailOut), offers=[offer_out(o) for o in v.offers])


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    category: str | None = None,
    q: str | None = Query(None, description="Search name, SKU or description"),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(Product).options(selectinload(Product.offers).selectinload(VendorOffer.vendor)).order_by(Product.category, Product.name)
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like), Product.description.ilike(like)))
    products = (await db.scalars(stmt)).all()
    return [
        ProductOut(**_fields(p, ProductOut), offers=sorted((offer_out(o) for o in p.offers), key=lambda o: o.unit_price))
        for p in products
    ]


# ---------- catalogue management ----------

@router.post("/vendors", response_model=VendorOut, status_code=201)
async def create_vendor(body: VendorIn, db: AsyncSession = Depends(get_session), user: User = Depends(manage_catalog)):
    if await db.scalar(select(Vendor.id).where(func.lower(Vendor.name) == body.name.lower())):
        raise HTTPException(409, f"A vendor named {body.name!r} already exists")
    v = Vendor(name=body.name, contact_email=body.contact_email, country=body.country.upper(), rating=body.rating)
    db.add(v)
    await db.flush()
    _audit(db, user, "vendor.created", "vendor", v.id, {"name": v.name})
    await db.commit()
    await db.refresh(v)
    return v


@router.patch("/vendors/{vendor_id}", response_model=VendorOut)
async def update_vendor(vendor_id: uuid.UUID, body: VendorPatch, db: AsyncSession = Depends(get_session),
                        user: User = Depends(manage_catalog)):
    v = await db.get(Vendor, vendor_id)
    if not v:
        raise HTTPException(404, "Vendor not found")
    v.is_active = body.is_active
    _audit(db, user, "vendor.updated", "vendor", v.id, {"is_active": v.is_active})
    await db.commit()
    await db.refresh(v)
    return v


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductIn, db: AsyncSession = Depends(get_session), user: User = Depends(manage_catalog)):
    sku = body.sku.upper()
    if await db.scalar(select(Product.id).where(Product.sku == sku)):
        raise HTTPException(409, f"SKU {sku} already exists")
    p = Product(sku=sku, name=body.name, category=body.category.lower(), description=body.description, unit=body.unit,
                specs=body.specs)
    db.add(p)
    await db.flush()
    _audit(db, user, "product.created", "product", p.id, {"sku": sku, "name": p.name})
    await db.commit()
    return ProductOut(**_fields(p, ProductOut), offers=[])


async def _offer(db: AsyncSession, offer_id: uuid.UUID) -> VendorOffer | None:
    return await db.scalar(select(VendorOffer).where(VendorOffer.id == offer_id).options(selectinload(VendorOffer.vendor)))


@router.post("/offers", response_model=OfferOut, status_code=201)
async def create_offer(body: OfferIn, db: AsyncSession = Depends(get_session), user: User = Depends(manage_catalog)):
    if not await db.get(Vendor, body.vendor_id):
        raise HTTPException(404, "Vendor not found")
    if not await db.get(Product, body.product_id):
        raise HTTPException(404, "Product not found")
    if await db.scalar(select(VendorOffer.id).where(VendorOffer.vendor_id == body.vendor_id, VendorOffer.product_id == body.product_id)):
        raise HTTPException(409, "This vendor already has an offer for this product; update it instead")
    if body.available_quantity and body.available_quantity < body.min_order_quantity:
        raise HTTPException(422, "Available quantity is below the minimum order quantity")
    o = VendorOffer(**body.model_dump())
    db.add(o)
    await db.flush()
    _audit(db, user, "offer.created", "vendor_offer", o.id,
           {"vendor_id": str(o.vendor_id), "product_id": str(o.product_id), "unit_price": str(o.unit_price)})
    await db.commit()
    return offer_out(await _offer(db, o.id))


@router.patch("/offers/{offer_id}", response_model=OfferOut)
async def update_offer(offer_id: uuid.UUID, body: OfferPatch, db: AsyncSession = Depends(get_session),
                       user: User = Depends(manage_catalog)):
    o = await _offer(db, offer_id)
    if not o:
        raise HTTPException(404, "Offer not found")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(o, k, v)
    _audit(db, user, "offer.updated", "vendor_offer", o.id, {k: str(v) for k, v in changes.items()})
    await db.commit()
    return offer_out(await _offer(db, offer_id))


# ---------- starter catalogue (one-click import) ----------

@router.get("/catalog/starter", response_model=StarterCatalog)
async def starter_catalog(db: AsyncSession = Depends(get_session), _: User = Depends(manage_catalog)):
    """What the starter catalogue contains and which parts are already in this catalogue."""
    from seed.catalog import OFFERS, PRODUCTS, VENDORS

    have_v = set((await db.scalars(select(Vendor.name))).all())
    have_p = set((await db.scalars(select(Product.sku))).all())
    return StarterCatalog(
        vendors=[{"name": n, "contact_email": e, "country": c, "rating": r, "offers": sum(1 for o in OFFERS if o[0] == n),
                  "imported": n in have_v} for n, e, c, r in VENDORS],
        products=[{"sku": s, "name": n, "category": c, "description": d, "offers": sum(1 for o in OFFERS if o[1] == s),
                   "imported": s in have_p} for s, n, c, d, _u, _sp in PRODUCTS],
    )


@router.post("/catalog/starter/import")
async def import_starter(body: StarterImportIn, db: AsyncSession = Depends(get_session), user: User = Depends(manage_catalog)):
    """Import all of the starter catalogue, or chosen vendors / products. Safe to repeat: nothing is duplicated
    or overwritten. Offers come along whenever both their vendor and product are in the catalogue."""
    from seed.catalog import PRODUCTS, VENDORS, load_catalog

    known_v, known_p = {v[0] for v in VENDORS}, {p[0] for p in PRODUCTS}
    unknown = sorted(set(body.vendors or []) - known_v) + sorted(set(body.skus or []) - known_p)
    if unknown:
        raise HTTPException(422, f"Not in the starter catalogue: {', '.join(unknown)}")
    db.add(AuditLog(actor_type=ActorType.USER, actor_user_id=user.id, action="catalog.starter_imported", entity_type="catalog",
                    entity_id=None, details={"vendors": body.vendors, "skus": body.skus}))
    added = await load_catalog(db, vendors=body.vendors, skus=body.skus)
    return {"added": added}
