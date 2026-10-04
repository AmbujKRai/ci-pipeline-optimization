"""Product catalogue: public browsing plus admin management."""

from __future__ import annotations

from decimal import Decimal
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select

from app.deps import AdminUser, DbSession
from app.models import Product
from app.schemas import (
    Category,
    ProductCreate,
    ProductOut,
    ProductPage,
    ProductUpdate,
    RestockRequest,
)
from app.services import inventory
from app.services.money import to_paise

router = APIRouter(prefix="/products", tags=["products"])

SORT_ORDERS = {
    "name": Product.name.asc(),
    "-name": Product.name.desc(),
    "price": Product.price_paise.asc(),
    "-price": Product.price_paise.desc(),
    "newest": Product.created_at.desc(),
}
SortKey = Literal["name", "-name", "price", "-price", "newest"]


def _like_pattern(text: str) -> str:
    escaped = text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _get_or_404(db: DbSession, product_id: int, *, include_inactive: bool = False) -> Product:
    product = db.get(Product, product_id)
    if product is None or (not product.is_active and not include_inactive):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found")
    return product


@router.get("", response_model=ProductPage, summary="Browse and search the catalogue")
def list_products(
    db: DbSession,
    q: Annotated[str | None, Query(max_length=60, description="Search name or SKU")] = None,
    category: Category | None = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    in_stock: bool = False,
    sort: SortKey = "name",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductPage:
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "min_price exceeds max_price")

    query = select(Product).where(Product.is_active.is_(True))
    if q and q.strip():
        pattern = _like_pattern(q)
        query = query.where(
            or_(
                func.lower(Product.name).like(pattern, escape="\\"),
                func.lower(Product.sku).like(pattern, escape="\\"),
            )
        )
    if category is not None:
        query = query.where(Product.category == category)
    if min_price is not None:
        query = query.where(Product.price_paise >= to_paise(min_price))
    if max_price is not None:
        query = query.where(Product.price_paise <= to_paise(max_price))
    if in_stock:
        query = query.where(Product.stock > 0)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(SORT_ORDERS[sort], Product.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return ProductPage(
        items=[ProductOut.from_model(product) for product in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, ceil(total / page_size)),
    )


@router.get("/low-stock", response_model=list[ProductOut], summary="Products running out (admin)")
def low_stock_products(
    db: DbSession, _admin: AdminUser, threshold: Annotated[int, Query(ge=0, le=1000)] = 5
) -> list[ProductOut]:
    return [ProductOut.from_model(product) for product in inventory.low_stock(db, threshold)]


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: DbSession) -> ProductOut:
    return ProductOut.from_model(_get_or_404(db, product_id))


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: DbSession, _admin: AdminUser) -> ProductOut:
    if db.scalar(select(Product.id).where(Product.sku == payload.sku)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a product with this SKU already exists")
    product = Product(
        sku=payload.sku,
        name=payload.name.strip(),
        description=payload.description,
        category=payload.category,
        price_paise=to_paise(payload.price),
        stock=payload.stock,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return ProductOut.from_model(product)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int, payload: ProductUpdate, db: DbSession, _admin: AdminUser
) -> ProductOut:
    product = _get_or_404(db, product_id, include_inactive=True)
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if "price" in changes:
        product.price_paise = to_paise(changes.pop("price"))
    for field, value in changes.items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return ProductOut.from_model(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: DbSession, _admin: AdminUser) -> Response:
    """Soft delete: the product disappears from the catalogue but old orders keep their link."""
    product = _get_or_404(db, product_id)
    product.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{product_id}/restock", response_model=ProductOut)
def restock_product(
    product_id: int, payload: RestockRequest, db: DbSession, _admin: AdminUser
) -> ProductOut:
    product = _get_or_404(db, product_id, include_inactive=True)
    inventory.restock(product, payload.quantity)
    db.commit()
    db.refresh(product)
    return ProductOut.from_model(product)
