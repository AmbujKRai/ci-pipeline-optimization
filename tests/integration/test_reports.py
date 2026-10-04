"""Sales report checks against independently computed expectations.

Each test seeds its own order history (hundreds to thousands of orders) so the SQL
aggregates are exercised on realistic volumes.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.models import Order, OrderItem, Product, User
from app.services.money import from_paise
from app.services.reports import REVENUE_STATUSES, sales_report
from app.services.tax import CATEGORIES, gst_rate
from tests.conftest import login

pytestmark = pytest.mark.slow

SIZES = [500, 1_000, 2_000, 4_000]
STATUSES = ("pending", "paid", "shipped", "delivered", "cancelled")
START = datetime(2026, 3, 1)


@dataclass(frozen=True)
class SeededLine:
    product_id: int
    name: str
    category: str
    quantity: int
    line_total: int


@dataclass(frozen=True)
class SeededOrder:
    status: str
    created_at: datetime
    total: int
    lines: tuple[SeededLine, ...]


def seed_orders(db, count: int, seed: int) -> list[SeededOrder]:
    rng = random.Random(seed)
    user = User(
        email=f"report{seed}@example.com",
        full_name="Report User",
        password_hash="!",
        role="customer",
    )
    products = [
        Product(
            sku=f"RPT-{seed}-{index:03d}",
            name=f"Report product {index:03d}",
            category=CATEGORIES[index % len(CATEGORIES)],
            price_paise=rng.randint(5_000, 500_000),
            stock=1_000_000,
        )
        for index in range(40)
    ]
    db.add(user)
    db.add_all(products)
    db.commit()

    seeded: list[SeededOrder] = []
    orders: list[Order] = []
    for _ in range(count):
        created = START + timedelta(days=rng.randint(0, 29), minutes=rng.randint(0, 1_439))
        lines = []
        for product in rng.sample(products, rng.randint(1, 4)):
            quantity = rng.randint(1, 5)
            lines.append(
                SeededLine(
                    product.id,
                    product.name,
                    product.category,
                    quantity,
                    product.price_paise * quantity,
                )
            )
        subtotal = sum(line.line_total for line in lines)
        tax = subtotal * 18 // 100
        record = SeededOrder(rng.choice(STATUSES), created, subtotal + tax, tuple(lines))
        seeded.append(record)
        orders.append(
            Order(
                user=user,
                status=record.status,
                shipping_state="MH",
                subtotal_paise=subtotal,
                discount_paise=0,
                taxable_paise=subtotal,
                cgst_paise=tax // 2,
                sgst_paise=tax - tax // 2,
                igst_paise=0,
                shipping_paise=0,
                total_paise=record.total,
                created_at=created,
                updated_at=created,
                items=[
                    OrderItem(
                        product_id=line.product_id,
                        quantity=line.quantity,
                        unit_price_paise=line.line_total // line.quantity,
                        line_total_paise=line.line_total,
                        tax_rate=gst_rate(line.category),
                    )
                    for line in lines
                ],
            )
        )
    db.add_all(orders)
    db.commit()
    return seeded


def earning(seeded: list[SeededOrder]) -> list[SeededOrder]:
    return [order for order in seeded if order.status in REVENUE_STATUSES]


@pytest.mark.parametrize("size", SIZES)
def test_orders_are_counted_by_status(db, size):
    seeded = seed_orders(db, size, seed=size)
    report = sales_report(db)
    expected = Counter(order.status for order in seeded)
    assert report.orders_by_status == {status: expected.get(status, 0) for status in STATUSES}
    assert report.order_count == size


@pytest.mark.parametrize("size", SIZES)
def test_revenue_and_average_order_value(db, size):
    paid = earning(seed_orders(db, size, seed=size + 1))
    report = sales_report(db)
    revenue = sum(order.total for order in paid)
    assert report.revenue_paise == revenue
    assert report.paid_order_count == len(paid)
    assert report.average_order_value_paise == (2 * revenue + len(paid)) // (2 * len(paid))


@pytest.mark.parametrize("size", SIZES)
def test_category_breakdown(db, size):
    paid = earning(seed_orders(db, size, seed=size + 2))
    revenue: dict[str, int] = defaultdict(int)
    units: dict[str, int] = defaultdict(int)
    for order in paid:
        for line in order.lines:
            revenue[line.category] += line.line_total
            units[line.category] += line.quantity
    report = sales_report(db)
    assert {row.category: (row.revenue_paise, row.units) for row in report.by_category} == {
        category: (revenue[category], units[category]) for category in revenue
    }
    revenues = [row.revenue_paise for row in report.by_category]
    assert revenues == sorted(revenues, reverse=True)


@pytest.mark.parametrize("size", SIZES)
def test_top_products(db, size):
    paid = earning(seed_orders(db, size, seed=size + 3))
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for order in paid:
        for line in order.lines:
            totals[line.name][0] += line.line_total
            totals[line.name][1] += line.quantity
    expected = sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))[:10]
    report = sales_report(db, top_n=10)
    assert [(row.name, row.revenue_paise, row.units) for row in report.top_products] == [
        (name, revenue, units) for name, (revenue, units) in expected
    ]


@pytest.mark.parametrize("size", SIZES)
def test_daily_revenue_series(db, size):
    paid = earning(seed_orders(db, size, seed=size + 4))
    per_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for order in paid:
        day = order.created_at.date().isoformat()
        per_day[day][0] += 1
        per_day[day][1] += order.total
    report = sales_report(db)
    assert [(row.day, row.orders, row.revenue_paise) for row in report.daily] == [
        (day, count, revenue) for day, (count, revenue) in sorted(per_day.items())
    ]


@pytest.mark.parametrize("size", SIZES)
def test_date_window_filters_orders(db, size):
    seeded = seed_orders(db, size, seed=size + 5)
    start, end = START + timedelta(days=10), START + timedelta(days=20)
    window = [order for order in seeded if start <= order.created_at < end]
    report = sales_report(db, start, end)
    assert report.order_count == len(window)
    assert report.revenue_paise == sum(order.total for order in earning(window))


@pytest.mark.parametrize("size", SIZES)
def test_report_endpoint_matches_the_service(client, db, make_user, size):
    seed_orders(db, size, seed=size + 6)
    admin = make_user(role="admin", email="boss@example.com")
    body = client.get(
        "/reports/sales",
        params={"start": "2026-03-01", "end": "2026-03-15"},
        headers=login(client, admin.email),
    ).json()
    expected = sales_report(db, START, START + timedelta(days=15))
    assert body["order_count"] == expected.order_count
    assert body["paid_order_count"] == expected.paid_order_count
    assert body["revenue"] == str(from_paise(expected.revenue_paise))
    assert len(body["daily"]) == len(expected.daily)


def test_empty_report(db):
    report = sales_report(db)
    assert report.order_count == 0
    assert report.revenue_paise == 0
    assert report.average_order_value_paise == 0
    assert report.daily == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"start": START, "end": START}, "before"),
        ({"top_n": 0}, "top_n"),
    ],
)
def test_report_arguments_are_validated(db, kwargs, message):
    with pytest.raises(ValueError, match=message):
        sales_report(db, **kwargs)


def test_report_endpoint_is_admin_only(client, customer_headers):
    assert client.get("/reports/sales", headers=customer_headers).status_code == 403


def test_report_endpoint_rejects_an_inverted_range(client, admin_headers):
    response = client.get(
        "/reports/sales", params={"start": "2026-03-10", "end": "2026-03-01"}, headers=admin_headers
    )
    assert response.status_code == 422
