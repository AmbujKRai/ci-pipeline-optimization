"""Stock management: reserve, release and restock products."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product

MAX_RESTOCK_QUANTITY = 100_000


class InsufficientStockError(Exception):
    def __init__(self, product_id: int, requested: int, available: int) -> None:
        super().__init__(f"product {product_id}: requested {requested}, only {available} in stock")
        self.product_id = product_id
        self.requested = requested
        self.available = available


class InactiveProductError(Exception):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"product {product_id} is not available for sale")
        self.product_id = product_id


def _check_quantity(quantity: int) -> None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")


def reserve(product: Product, quantity: int) -> None:
    """Take `quantity` units out of stock for an order."""
    _check_quantity(quantity)
    if not product.is_active:
        raise InactiveProductError(product.id)
    if product.stock < quantity:
        raise InsufficientStockError(product.id, quantity, product.stock)
    product.stock -= quantity


def release(product: Product, quantity: int) -> None:
    """Put reserved units back, for example when an order is cancelled."""
    _check_quantity(quantity)
    product.stock += quantity


def restock(product: Product, quantity: int) -> None:
    _check_quantity(quantity)
    if quantity > MAX_RESTOCK_QUANTITY:
        raise ValueError(f"cannot restock more than {MAX_RESTOCK_QUANTITY} units at once")
    product.stock += quantity


def low_stock(session: Session, threshold: int = 5) -> list[Product]:
    """Active products at or below `threshold` units, lowest stock first."""
    if threshold < 0:
        raise ValueError("threshold cannot be negative")
    query = (
        select(Product)
        .where(Product.is_active.is_(True), Product.stock <= threshold)
        .order_by(Product.stock, Product.name)
    )
    return list(session.scalars(query))
