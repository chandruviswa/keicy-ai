import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.schemas import OfferOut, ProductOut, VendorDetailOut, VendorOut
from app.db.session import get_session
from app.models import Product, Vendor, VendorOffer

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
