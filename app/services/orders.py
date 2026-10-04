"""Order lifecycle: quoting, creation, payment, shipping and cancellation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import Coupon, Order, OrderItem, Product, User
from app.services import inventory
from app.services.payments import PaymentDeclinedError, PaymentGateway, charge_with_retry
from app.services.pricing import MAX_LINE_QUANTITY, CartLine, CouponError, Quote, build_quote
from app.services.tax import gst_rate, normalise_state

ORDER_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"paid", "cancelled"}),
    "paid": frozenset({"shipped", "cancelled"}),
    "shipped": frozenset({"delivered"}),
    "delivered": frozenset(),
    "cancelled": frozenset(),
}
ORDER_STATUSES: tuple[str, ...] = tuple(ORDER_TRANSITIONS)
MAX_ORDER_LINES = 50


class InvalidTransitionError(Exception):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"cannot move an order from {current!r} to {target!r}")
        self.current = current
        self.target = target


class ProductNotFoundError(Exception):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"product {product_id} does not exist")
        self.product_id = product_id


@dataclass(frozen=True)
class OrderLineRequest:
    product_id: int
    quantity: int


def can_transition(current: str, target: str) -> bool:
    if current not in ORDER_TRANSITIONS:
        raise ValueError(f"unknown order status: {current!r}")
    return target in ORDER_TRANSITIONS[current]


def transition(order: Order, target: str) -> None:
    if not can_transition(order.status, target):
        raise InvalidTransitionError(order.status, target)
    order.status = target
    order.updated_at = utcnow()


def merge_lines(lines: Iterable[OrderLineRequest]) -> list[OrderLineRequest]:
    """Combine repeated products into one line each, keeping first-seen order."""
    totals: dict[int, int] = {}
    for line in lines:
        if line.quantity < 1:
            raise ValueError("quantity must be at least 1")
        totals[line.product_id] = totals.get(line.product_id, 0) + line.quantity
    if not totals:
        raise ValueError("order has no items")
    if len(totals) > MAX_ORDER_LINES:
        raise ValueError(f"an order can contain at most {MAX_ORDER_LINES} different products")
    for product_id, quantity in totals.items():
        if quantity > MAX_LINE_QUANTITY:
            raise ValueError(f"product {product_id}: at most {MAX_LINE_QUANTITY} units per order")
    return [OrderLineRequest(product_id, quantity) for product_id, quantity in totals.items()]


def find_coupon(session: Session, code: str | None) -> Coupon | None:
    if code is None or not code.strip():
        return None
    coupon = session.scalar(select(Coupon).where(Coupon.code == code.strip().upper()))
    if coupon is None:
        raise CouponError("not_found")
    return coupon


def _load_products(session: Session, product_ids: list[int]) -> dict[int, Product]:
    found = session.scalars(select(Product).where(Product.id.in_(product_ids)))
    products = {product.id: product for product in found}
    for product_id in product_ids:
        if product_id not in products:
            raise ProductNotFoundError(product_id)
        if not products[product_id].is_active:
            raise inventory.InactiveProductError(product_id)
    return products


def _prepare(
    session: Session,
    lines: Iterable[OrderLineRequest],
    shipping_state: str,
    coupon_code: str | None,
    now: datetime | None,
) -> tuple[list[OrderLineRequest], dict[int, Product], Coupon | None, Quote]:
    merged = merge_lines(lines)
    products = _load_products(session, [line.product_id for line in merged])
    coupon = find_coupon(session, coupon_code)
    cart = [
        CartLine(
            product_id=line.product_id,
            unit_price_paise=products[line.product_id].price_paise,
            quantity=line.quantity,
            tax_rate=gst_rate(products[line.product_id].category),
        )
        for line in merged
    ]
    return merged, products, coupon, build_quote(cart, shipping_state, coupon, now)


def quote_order(
    session: Session,
    lines: Iterable[OrderLineRequest],
    shipping_state: str,
    coupon_code: str | None = None,
    now: datetime | None = None,
) -> Quote:
    """Price an order without reserving stock or saving anything."""
    return _prepare(session, lines, shipping_state, coupon_code, now)[3]


def create_order(
    session: Session,
    user: User,
    lines: Iterable[OrderLineRequest],
    shipping_state: str,
    coupon_code: str | None = None,
    now: datetime | None = None,
) -> Order:
    """Reserve stock and save a pending order. Nothing is saved if any line fails."""
    merged, products, coupon, quote = _prepare(session, lines, shipping_state, coupon_code, now)
    try:
        for line in merged:
            inventory.reserve(products[line.product_id], line.quantity)
    except Exception:
        session.rollback()
        raise

    timestamp = now or utcnow()
    order = Order(
        user_id=user.id,
        status="pending",
        shipping_state=normalise_state(shipping_state),
        subtotal_paise=quote.subtotal_paise,
        discount_paise=quote.discount_paise,
        taxable_paise=quote.taxable_paise,
        cgst_paise=quote.cgst_paise,
        sgst_paise=quote.sgst_paise,
        igst_paise=quote.igst_paise,
        shipping_paise=quote.shipping_paise,
        total_paise=quote.total_paise,
        coupon_code=coupon.code if coupon else None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    order.items = [
        OrderItem(
            product_id=line.product_id,
            quantity=line.quantity,
            unit_price_paise=line.unit_price_paise,
            line_total_paise=line.line_total_paise,
            tax_rate=line.tax_rate,
        )
        for line in quote.lines
    ]
    if coupon is not None:
        coupon.used_count += 1
    session.add(order)
    session.commit()
    return order


def pay_order(session: Session, order: Order, gateway: PaymentGateway, card_token: str) -> Order:
    if order.status != "pending":
        raise InvalidTransitionError(order.status, "paid")
    result = charge_with_retry(
        gateway, order.total_paise, card_token, idempotency_key=f"order-{order.id}:{card_token}"
    )
    if not result.approved:
        raise PaymentDeclinedError(result.decline_reason or "declined")
    order.payment_ref = result.reference
    transition(order, "paid")
    session.commit()
    return order


def ship_order(session: Session, order: Order) -> Order:
    transition(order, "shipped")
    session.commit()
    return order


def deliver_order(session: Session, order: Order) -> Order:
    transition(order, "delivered")
    session.commit()
    return order


def cancel_order(session: Session, order: Order, gateway: PaymentGateway) -> bool:
    """Cancel an order, return its stock and refund it if it was paid.

    Returns True when a refund was issued.
    """
    if not can_transition(order.status, "cancelled"):
        raise InvalidTransitionError(order.status, "cancelled")
    refunded = False
    if order.status == "paid" and order.payment_ref:
        gateway.refund(order.payment_ref, order.total_paise)
        refunded = True
    for item in order.items:
        inventory.release(item.product, item.quantity)
    if order.coupon_code:
        coupon = session.scalar(select(Coupon).where(Coupon.code == order.coupon_code))
        if coupon is not None and coupon.used_count > 0:
            coupon.used_count -= 1
    transition(order, "cancelled")
    session.commit()
    return refunded


def get_order_for(session: Session, order_id: int, user: User) -> Order | None:
    """The order if `user` may see it (its owner or an admin), otherwise None."""
    order = session.get(Order, order_id)
    if order is None:
        return None
    if user.role != "admin" and order.user_id != user.id:
        return None
    return order


def list_orders(
    session: Session, user: User, status: str | None = None, limit: int = 50, offset: int = 0
) -> list[Order]:
    query = select(Order).order_by(Order.created_at.desc(), Order.id.desc())
    if user.role != "admin":
        query = query.where(Order.user_id == user.id)
    if status is not None:
        if status not in ORDER_TRANSITIONS:
            raise ValueError(f"unknown order status: {status!r}")
        query = query.where(Order.status == status)
    return list(session.scalars(query.limit(limit).offset(offset)))
