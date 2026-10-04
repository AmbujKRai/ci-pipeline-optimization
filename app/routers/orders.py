"""Quotes, orders and the order lifecycle."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.deps import AdminUser, CurrentUser, DbSession, GatewayDep, NotifierDep
from app.models import Order, User
from app.schemas import (
    OrderActionOut,
    OrderCreate,
    OrderOut,
    OrderStatus,
    PaymentRequest,
    QuoteOut,
)
from app.services import orders as order_service
from app.services.notifications import (
    Notifier,
    cancellation_notice,
    order_confirmation,
    shipment_notice,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])


def _lines(payload: OrderCreate) -> list[order_service.OrderLineRequest]:
    return [
        order_service.OrderLineRequest(line.product_id, line.quantity) for line in payload.items
    ]


def _notify(notifier: Notifier, user: User, message: tuple[str, str]) -> None:
    """E-mail is best effort: a failed notification must never fail the order itself."""
    subject, body = message
    try:
        notifier.send(user.email, subject, body)
    except (ValueError, OSError) as exc:
        logger.warning("could not notify %s: %s", user.email, exc)


def _visible_order(db: DbSession, order_id: int, user: User) -> Order:
    order = order_service.get_order_for(db, order_id, user)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return order


@router.post("/quote", response_model=QuoteOut, summary="Price a cart without placing an order")
def quote(payload: OrderCreate, db: DbSession) -> QuoteOut:
    result = order_service.quote_order(
        db, _lines(payload), payload.shipping_state, payload.coupon_code
    )
    return QuoteOut.from_quote(result)


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def place_order(
    payload: OrderCreate, db: DbSession, user: CurrentUser, notifier: NotifierDep
) -> OrderOut:
    order = order_service.create_order(
        db, user, _lines(payload), payload.shipping_state, payload.coupon_code
    )
    _notify(notifier, user, order_confirmation(order, user))
    return OrderOut.from_model(order)


@router.get("", response_model=list[OrderOut], summary="Your orders (admins see every order)")
def list_orders(
    db: DbSession,
    user: CurrentUser,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OrderOut]:
    orders = order_service.list_orders(db, user, order_status, limit, offset)
    return [OrderOut.from_model(order) for order in orders]


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DbSession, user: CurrentUser) -> OrderOut:
    return OrderOut.from_model(_visible_order(db, order_id, user))


@router.post("/{order_id}/pay", response_model=OrderOut)
def pay_order(
    order_id: int, payload: PaymentRequest, db: DbSession, user: CurrentUser, gateway: GatewayDep
) -> OrderOut:
    order = _visible_order(db, order_id, user)
    order_service.pay_order(db, order, gateway, payload.card_token)
    return OrderOut.from_model(order)


@router.post("/{order_id}/ship", response_model=OrderOut, summary="Mark as shipped (admin)")
def ship_order(order_id: int, db: DbSession, _admin: AdminUser, notifier: NotifierDep) -> OrderOut:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    order_service.ship_order(db, order)
    _notify(notifier, order.user, shipment_notice(order, order.user))
    return OrderOut.from_model(order)


@router.post("/{order_id}/deliver", response_model=OrderOut, summary="Mark as delivered (admin)")
def deliver_order(order_id: int, db: DbSession, _admin: AdminUser) -> OrderOut:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    order_service.deliver_order(db, order)
    return OrderOut.from_model(order)


@router.post("/{order_id}/cancel", response_model=OrderActionOut)
def cancel_order(
    order_id: int,
    db: DbSession,
    user: CurrentUser,
    gateway: GatewayDep,
    notifier: NotifierDep,
) -> OrderActionOut:
    order = _visible_order(db, order_id, user)
    refunded = order_service.cancel_order(db, order, gateway)
    _notify(notifier, order.user, cancellation_notice(order, order.user, refunded))
    return OrderActionOut(order=OrderOut.from_model(order), refunded=refunded)
