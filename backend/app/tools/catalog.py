"""Product and vendor tools (read-only). Every record returned carries a `source` reference."""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.agents.core.tools import ToolBusinessError, ToolContext
from app.models import Product, Vendor, VendorOffer
from app.tools.common import money, offer_issues, registry, source

# ---------- shared result shapes ----------


class ProductSummary(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    category: str
    description: str
    specs: dict[str, Any]
    offer_count: int
    lowest_unit_price: Decimal | None
    source: dict[str, str]


class OfferQuote(BaseModel):
    offer_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_rating: Decimal
    product_id: uuid.UUID
    sku: str
    unit_price: Decimal
    currency: str
    quantity: int
    line_total: Decimal
    available_quantity: int
    min_order_quantity: int
    lead_time_days: int
    valid_until: str
    specs: dict[str, Any]  # product specs overlaid with this vendor's variant
    eligible: bool
    issues: list[str]
    source: dict[str, str]


def _quote(o: VendorOffer, quantity: int) -> OfferQuote:
    issues = offer_issues(o, quantity)
    return OfferQuote(
        offer_id=o.id, vendor_id=o.vendor_id, vendor_name=o.vendor.name, vendor_rating=o.vendor.rating,
        product_id=o.product_id, sku=o.product.sku, unit_price=o.unit_price, currency=o.currency, quantity=quantity,
        line_total=money(o.unit_price * quantity), available_quantity=o.available_quantity,
        min_order_quantity=o.min_order_quantity, lead_time_days=o.lead_time_days, valid_until=o.valid_until.isoformat(),
        specs={**o.product.specs, **o.specs}, eligible=not issues, issues=issues, source=source("vendor_offers", o.id),
    )


_OFFER_LOAD = (selectinload(VendorOffer.vendor), selectinload(VendorOffer.product))

# ---------- search_catalog ----------


class SearchCatalogArgs(BaseModel):
    query: str | None = Field(None, max_length=200, description="Words to match in name, SKU or description")
    category: str | None = Field(None, max_length=64)
    limit: int = Field(20, ge=1, le=50)


class SearchCatalogResult(BaseModel):
    products: list[ProductSummary]


@registry.tool("search_catalog", args=SearchCatalogArgs, result=SearchCatalogResult, group="catalog",
               description="Search the product catalogue by keywords and/or category.")
async def search_catalog(ctx: ToolContext, a: SearchCatalogArgs) -> SearchCatalogResult:
    stmt = select(Product).options(selectinload(Product.offers)).order_by(Product.name).limit(a.limit)
    if a.category:
        stmt = stmt.where(Product.category == a.category)
    if a.query:
        # Match any word, so "laptop 16GB" still finds laptops.
        words = [w for w in a.query.split() if len(w) > 1][:8]
        stmt = stmt.where(or_(*(c.ilike(f"%{w}%") for w in words for c in (Product.name, Product.sku, Product.description, Product.category))))
    products = (await ctx.db.scalars(stmt)).all()
    return SearchCatalogResult(products=[
        ProductSummary(
            product_id=p.id, sku=p.sku, name=p.name, category=p.category, description=p.description, specs=p.specs,
            offer_count=len(p.offers), lowest_unit_price=min((o.unit_price for o in p.offers), default=None),
            source=source("products", p.id),
        ) for p in products
    ])


# ---------- get_product_details ----------


class ProductRef(BaseModel):
    product_id: uuid.UUID | None = None
    sku: str | None = None

    @model_validator(mode="after")
    def one_of(self):
        if not (self.product_id or self.sku):
            raise ValueError("give product_id or sku")
        return self


class ProductDetails(ProductSummary):
    unit: str
    offers: list[OfferQuote]


@registry.tool("get_product_details", args=ProductRef, result=ProductDetails, group="catalog",
               description="Full product record with every vendor offer (quoted for quantity 1).")
async def get_product_details(ctx: ToolContext, a: ProductRef) -> ProductDetails:
    stmt = select(Product).options(selectinload(Product.offers).options(*_OFFER_LOAD))
    stmt = stmt.where(Product.id == a.product_id) if a.product_id else stmt.where(Product.sku == a.sku)
    p = await ctx.db.scalar(stmt)
    if not p:
        raise ToolBusinessError(f"Product not found: {a.product_id or a.sku}")
    offers = sorted((_quote(o, 1) for o in p.offers), key=lambda q: q.unit_price)
    return ProductDetails(
        product_id=p.id, sku=p.sku, name=p.name, category=p.category, description=p.description, specs=p.specs,
        unit=p.unit, offer_count=len(offers), lowest_unit_price=offers[0].unit_price if offers else None,
        offers=offers, source=source("products", p.id),
    )


# ---------- search_vendors ----------


class SearchVendorsArgs(BaseModel):
    query: str | None = Field(None, max_length=200)
    category: str | None = Field(None, description="Only vendors with offers in this product category")
    min_rating: Decimal | None = Field(None, ge=0, le=5)


class VendorSummary(BaseModel):
    vendor_id: uuid.UUID
    name: str
    country: str
    rating: Decimal
    categories: list[str]
    offer_count: int
    source: dict[str, str]


class SearchVendorsResult(BaseModel):
    vendors: list[VendorSummary]


@registry.tool("search_vendors", args=SearchVendorsArgs, result=SearchVendorsResult, group="catalog",
               description="Find active vendors, optionally by name, category served and minimum rating.")
async def search_vendors(ctx: ToolContext, a: SearchVendorsArgs) -> SearchVendorsResult:
    stmt = select(Vendor).where(Vendor.is_active.is_(True)).options(selectinload(Vendor.offers).selectinload(VendorOffer.product)).order_by(Vendor.rating.desc())
    if a.query:
        stmt = stmt.where(Vendor.name.ilike(f"%{a.query}%"))
    if a.min_rating is not None:
        stmt = stmt.where(Vendor.rating >= a.min_rating)
    out = []
    for v in (await ctx.db.scalars(stmt)).all():
        cats = sorted({o.product.category for o in v.offers})
        if a.category and a.category not in cats:
            continue
        out.append(VendorSummary(vendor_id=v.id, name=v.name, country=v.country, rating=v.rating, categories=cats,
                                 offer_count=len(v.offers), source=source("vendors", v.id)))
    return SearchVendorsResult(vendors=out)


# ---------- get_vendor_quotes ----------


class VendorQuotesArgs(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=1_000_000)
    include_ineligible: bool = Field(True, description="Also return expired / out-of-stock offers, flagged with issues")


class VendorQuotesResult(BaseModel):
    product_id: uuid.UUID
    quantity: int
    quotes: list[OfferQuote]
    eligible_count: int


@registry.tool("get_vendor_quotes", args=VendorQuotesArgs, result=VendorQuotesResult, group="catalog",
               description="Price every vendor offer for a product at the requested quantity, cheapest first.")
async def get_vendor_quotes(ctx: ToolContext, a: VendorQuotesArgs) -> VendorQuotesResult:
    offers = (await ctx.db.scalars(select(VendorOffer).where(VendorOffer.product_id == a.product_id).options(*_OFFER_LOAD))).all()
    if not offers and not await ctx.db.get(Product, a.product_id):
        raise ToolBusinessError(f"Product not found: {a.product_id}")
    quotes = sorted((_quote(o, a.quantity) for o in offers), key=lambda q: (not q.eligible, q.line_total))
    if not a.include_ineligible:
        quotes = [q for q in quotes if q.eligible]
    return VendorQuotesResult(product_id=a.product_id, quantity=a.quantity, quotes=quotes, eligible_count=sum(q.eligible for q in quotes))


# ---------- check_availability ----------


class AvailabilityArgs(BaseModel):
    offer_id: uuid.UUID
    quantity: int = Field(ge=1)


class AvailabilityResult(BaseModel):
    offer_id: uuid.UUID
    available: bool
    available_quantity: int
    lead_time_days: int
    issues: list[str]
    source: dict[str, str]


@registry.tool("check_availability", args=AvailabilityArgs, result=AvailabilityResult, group="catalog",
               description="Check whether an offer can supply a quantity now (stock, MOQ, expiry, vendor status).")
async def check_availability(ctx: ToolContext, a: AvailabilityArgs) -> AvailabilityResult:
    o = await ctx.db.scalar(select(VendorOffer).where(VendorOffer.id == a.offer_id).options(*_OFFER_LOAD))
    if not o:
        raise ToolBusinessError(f"Offer not found: {a.offer_id}")
    issues = offer_issues(o, a.quantity)
    return AvailabilityResult(offer_id=o.id, available=not issues, available_quantity=o.available_quantity,
                              lead_time_days=o.lead_time_days, issues=issues, source=source("vendor_offers", o.id))


# ---------- compare_offers ----------


class CompareOffersArgs(BaseModel):
    offer_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    quantity: int = Field(ge=1)


class CompareOffersResult(BaseModel):
    quantity: int
    rows: list[OfferQuote]  # eligible first, then by total cost
    cheapest_offer_id: uuid.UUID | None
    fastest_offer_id: uuid.UUID | None
    best_rated_offer_id: uuid.UUID | None
    missing_offer_ids: list[uuid.UUID]


@registry.tool("compare_offers", args=CompareOffersArgs, result=CompareOffersResult, group="catalog",
               description="Side-by-side comparison of specific offers at one quantity: totals, lead times, ratings, eligibility.")
async def compare_offers(ctx: ToolContext, a: CompareOffersArgs) -> CompareOffersResult:
    offers = (await ctx.db.scalars(select(VendorOffer).where(VendorOffer.id.in_(a.offer_ids)).options(*_OFFER_LOAD))).all()
    rows = sorted((_quote(o, a.quantity) for o in offers), key=lambda q: (not q.eligible, q.line_total, q.lead_time_days))
    eligible = [r for r in rows if r.eligible]
    found = {o.id for o in offers}
    return CompareOffersResult(
        quantity=a.quantity,
        rows=rows,
        cheapest_offer_id=min(eligible, key=lambda r: r.line_total).offer_id if eligible else None,
        fastest_offer_id=min(eligible, key=lambda r: r.lead_time_days).offer_id if eligible else None,
        best_rated_offer_id=max(eligible, key=lambda r: r.vendor_rating).offer_id if eligible else None,
        missing_offer_ids=[i for i in a.offer_ids if i not in found],
    )
