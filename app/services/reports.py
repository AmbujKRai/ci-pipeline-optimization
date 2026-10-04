"""Sales reporting with SQL aggregates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, Product
from app.services.orders import ORDER_STATUSES

# Orders that brought in money (cancelled and unpaid orders are excluded).
REVENUE_STATUSES: tuple[str, ...] = ("paid", "shipped", "delivered")


@dataclass(frozen=True)
class CategorySales:
    category: str
    revenue_paise: int
    units: int


@dataclass(frozen=True)
class ProductSales:
    product_id: int
    name: str
    units: int
    revenue_paise: int


@dataclass(frozen=True)
class DailySales:
    day: str
    orders: int
    revenue_paise: int


@dataclass(frozen=True)
class SalesReport:
    orders_by_status: dict[str, int]
    order_count: int
    paid_order_count: int
    revenue_paise: int
    average_order_value_paise: int
    by_category: list[CategorySales] = field(default_factory=list)
    top_products: list[ProductSales] = field(default_factory=list)
    daily: list[DailySales] = field(default_factory=list)


def _rounded_div(numerator: int, denominator: int) -> int:
    """Integer division rounded half-up (for non-negative values)."""
    return (2 * numerator + denominator) // (2 * denominator)


def sales_report(
    session: Session,
    start: datetime | None = None,
    end: datetime | None = None,
    top_n: int = 5,
) -> SalesReport:
    """Summarise orders created in [start, end).

    Revenue is the sum of order totals (including GST and shipping). Category and product
    figures use line values after bulk discounts, before coupons and tax.
    """
    if start is not None and end is not None and start >= end:
        raise ValueError("start must be before end")
    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    window = []
    if start is not None:
        window.append(Order.created_at >= start)
    if end is not None:
        window.append(Order.created_at < end)
    earning = [*window, Order.status.in_(REVENUE_STATUSES)]

    by_status = dict.fromkeys(ORDER_STATUSES, 0)
    for status, count in session.execute(
        select(Order.status, func.count(Order.id)).where(*window).group_by(Order.status)
    ):
        by_status[status] = count

    revenue, paid_count = session.execute(
        select(func.coalesce(func.sum(Order.total_paise), 0), func.count(Order.id)).where(*earning)
    ).one()

    line_revenue = func.sum(OrderItem.line_total_paise)
    category_rows = session.execute(
        select(Product.category, line_revenue, func.sum(OrderItem.quantity))
        .select_from(OrderItem)
        .join(OrderItem.product)
        .join(OrderItem.order)
        .where(*earning)
        .group_by(Product.category)
        .order_by(line_revenue.desc(), Product.category)
    ).all()

    product_rows = session.execute(
        select(
            Product.id,
            Product.name,
            func.sum(OrderItem.quantity).label("units"),
            line_revenue.label("revenue"),
        )
        .select_from(OrderItem)
        .join(OrderItem.product)
        .join(OrderItem.order)
        .where(*earning)
        .group_by(Product.id, Product.name)
        .order_by(desc("revenue"), Product.name)
        .limit(top_n)
    ).all()

    day = func.date(Order.created_at).label("day")
    daily_rows = session.execute(
        select(day, func.count(Order.id), func.sum(Order.total_paise))
        .where(*earning)
        .group_by(day)
        .order_by(day)
    ).all()

    return SalesReport(
        orders_by_status=by_status,
        order_count=sum(by_status.values()),
        paid_order_count=paid_count,
        revenue_paise=revenue,
        average_order_value_paise=_rounded_div(revenue, paid_count) if paid_count else 0,
        by_category=[CategorySales(cat, rev, units) for cat, rev, units in category_rows],
        top_products=[
            ProductSales(pid, name, units, rev) for pid, name, units, rev in product_rows
        ],
        daily=[DailySales(str(d), count, rev) for d, count, rev in daily_rows],
    )
