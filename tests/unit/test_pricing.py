import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.services.pricing import (
    FREE_SHIPPING_THRESHOLD_PAISE,
    STANDARD_SHIPPING_PAISE,
    CartLine,
    CouponError,
    allocate,
    build_quote,
    bulk_discount_percent,
    coupon_discount_paise,
    line_total_paise,
    shipping_fee_paise,
    validate_coupon,
)
from app.services.tax import STATE_CODES, InvalidStateError

NOW = datetime(2026, 6, 1, 12, 0, 0)


@dataclass
class FakeCoupon:
    code: str = "TEST"
    kind: str = "percent"
    value: int = 10
    max_discount_paise: int | None = None
    min_order_paise: int = 0
    expires_at: datetime | None = None
    usage_limit: int | None = None
    used_count: int = 0
    is_active: bool = True


# --- bulk discounts ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "percent"),
    [(1, 0), (9, 0), (10, 5), (19, 5), (20, 7), (49, 7), (50, 10), (1_000, 10)],
)
def test_bulk_discount_tiers(quantity, percent):
    assert bulk_discount_percent(quantity) == percent


@pytest.mark.parametrize(
    ("price", "quantity", "expected"),
    [
        (10_000, 1, 10_000),
        (10_000, 9, 90_000),
        (10_000, 10, 95_000),
        (10_000, 20, 186_000),
        (10_000, 50, 450_000),
        (999, 10, 9_490),  # 5 % of 9,990 is 499.5, rounded up to 500
    ],
)
def test_line_total_applies_bulk_discount(price, quantity, expected):
    assert line_total_paise(price, quantity) == expected


@pytest.mark.parametrize("quantity", [0, -1, 1_001])
def test_line_total_rejects_bad_quantity(quantity):
    with pytest.raises(ValueError, match="quantity"):
        line_total_paise(1_000, quantity)


def test_line_total_rejects_negative_price():
    with pytest.raises(ValueError, match="negative"):
        line_total_paise(-1, 1)


# --- coupons ----------------------------------------------------------------------------------


def test_valid_coupon_passes():
    validate_coupon(FakeCoupon(), 1_000, NOW)


@pytest.mark.parametrize(
    ("coupon", "subtotal", "reason"),
    [
        (FakeCoupon(is_active=False), 1_000, "inactive"),
        (FakeCoupon(expires_at=NOW), 1_000, "expired"),  # expiry instant is exclusive
        (FakeCoupon(expires_at=NOW - timedelta(seconds=1)), 1_000, "expired"),
        (FakeCoupon(usage_limit=5, used_count=5), 1_000, "usage_limit_reached"),
        (FakeCoupon(min_order_paise=150_000), 149_999, "minimum_not_met"),
    ],
)
def test_coupon_rejections(coupon, subtotal, reason):
    with pytest.raises(CouponError) as excinfo:
        validate_coupon(coupon, subtotal, NOW)
    assert excinfo.value.reason == reason


def test_coupon_is_valid_until_its_expiry():
    validate_coupon(FakeCoupon(expires_at=NOW + timedelta(seconds=1)), 1_000, NOW)


def test_coupon_minimum_is_inclusive():
    validate_coupon(FakeCoupon(min_order_paise=150_000), 150_000, NOW)


def test_coupon_with_uses_left_is_valid():
    validate_coupon(FakeCoupon(usage_limit=5, used_count=4), 1_000, NOW)


@pytest.mark.parametrize(
    ("coupon", "subtotal", "expected"),
    [
        (FakeCoupon(kind="percent", value=10), 100_000, 10_000),
        (FakeCoupon(kind="percent", value=10, max_discount_paise=20_000), 500_000, 20_000),
        (FakeCoupon(kind="percent", value=10, max_discount_paise=20_000), 150_000, 15_000),
        (FakeCoupon(kind="percent", value=15), 3_333, 500),  # 499.95
        (FakeCoupon(kind="percent", value=90), 1_000, 900),
        (FakeCoupon(kind="flat", value=20_000), 150_000, 20_000),
        (FakeCoupon(kind="flat", value=20_000), 15_000, 15_000),  # never more than the cart
    ],
)
def test_coupon_discount(coupon, subtotal, expected):
    assert coupon_discount_paise(coupon, subtotal) == expected


def test_unknown_coupon_kind_is_rejected():
    with pytest.raises(CouponError) as excinfo:
        coupon_discount_paise(FakeCoupon(kind="bogo"), 1_000)
    assert excinfo.value.reason == "unknown_kind"


# --- discount allocation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "weights", "expected"),
    [
        (100, [1, 1], [50, 50]),
        (101, [1, 1], [51, 50]),  # tie goes to the earlier line
        (100, [1, 1, 1], [34, 33, 33]),
        (10, [3, 7], [3, 7]),
        (1_000, [0, 5, 5], [0, 500, 500]),
        (7, [2, 2, 3], [2, 2, 3]),
        (0, [5, 10], [0, 0]),
        (0, [0, 0], [0, 0]),
        (5, [1], [5]),
    ],
)
def test_allocate_examples(total, weights, expected):
    assert allocate(total, weights) == expected


@pytest.mark.parametrize("seed", range(25))
def test_allocate_is_exact_and_proportional(seed):
    rng = random.Random(seed)
    weights = [rng.randint(0, 50_000) for _ in range(rng.randint(1, 8))]
    if sum(weights) == 0:
        weights[0] = 1
    total = rng.randint(0, sum(weights))
    shares = allocate(total, weights)
    assert sum(shares) == total
    for share, weight in zip(shares, weights, strict=True):
        exact = total * weight / sum(weights)
        assert abs(share - exact) < 1
        assert 0 <= share <= weight


@pytest.mark.parametrize(
    ("total", "weights", "message"),
    [(-1, [1], "total"), (1, [1, -1], "weights"), (5, [0, 0], "zero weights")],
)
def test_allocate_rejects_bad_input(total, weights, message):
    with pytest.raises(ValueError, match=message):
        allocate(total, weights)


# --- shipping ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("order_value", "fee"),
    [
        (0, 0),
        (-5, 0),
        (1, STANDARD_SHIPPING_PAISE),
        (FREE_SHIPPING_THRESHOLD_PAISE - 1, STANDARD_SHIPPING_PAISE),
        (FREE_SHIPPING_THRESHOLD_PAISE, 0),
        (500_000, 0),
    ],
)
def test_shipping_fee(order_value, fee):
    assert shipping_fee_paise(order_value) == fee


# --- full quotes ------------------------------------------------------------------------------


def test_quote_single_line_within_maharashtra():
    quote = build_quote([CartLine(1, 100_000, 1, 18)], "MH")
    assert (quote.subtotal_paise, quote.discount_paise) == (100_000, 0)
    assert (quote.cgst_paise, quote.sgst_paise, quote.igst_paise) == (9_000, 9_000, 0)
    assert quote.shipping_paise == 0
    assert quote.total_paise == 118_000


def test_small_order_pays_shipping():
    quote = build_quote([CartLine(1, 50_000, 1, 5)], "KA")
    assert quote.igst_paise == 2_500
    assert quote.shipping_paise == STANDARD_SHIPPING_PAISE
    assert quote.total_paise == 50_000 + 2_500 + STANDARD_SHIPPING_PAISE


def test_coupon_discount_is_shared_across_lines_before_tax():
    lines = [CartLine(1, 100_000, 1, 18), CartLine(2, 50_000, 2, 5)]
    quote = build_quote(lines, "KA", FakeCoupon(kind="flat", value=30_000), NOW)
    assert [line.coupon_share_paise for line in quote.lines] == [15_000, 15_000]
    assert [line.taxable_paise for line in quote.lines] == [85_000, 85_000]
    assert quote.igst_paise == 15_300 + 4_250
    assert quote.total_paise == 170_000 + 19_550


def test_bulk_discount_applies_before_coupon():
    quote = build_quote([CartLine(1, 10_000, 20, 12)], "MH", FakeCoupon(value=10), NOW)
    assert quote.subtotal_paise == 186_000
    assert quote.discount_paise == 18_600
    assert quote.cgst_paise == quote.sgst_paise == 10_044
    assert quote.total_paise == 167_400 + 20_088


def test_quote_rejects_empty_cart():
    with pytest.raises(ValueError, match="empty"):
        build_quote([], "MH")


def test_quote_rejects_unknown_state():
    with pytest.raises(InvalidStateError):
        build_quote([CartLine(1, 1_000, 1, 18)], "ZZ")


def test_quote_validates_the_coupon():
    expired = FakeCoupon(expires_at=NOW - timedelta(days=1))
    with pytest.raises(CouponError, match="expired"):
        build_quote([CartLine(1, 1_000, 1, 18)], "MH", expired, NOW)


@pytest.mark.parametrize("seed", range(15))
def test_random_carts_add_up(seed):
    rng = random.Random(seed)
    lines = [
        CartLine(i, rng.randint(100, 500_000), rng.randint(1, 60), rng.choice([5, 12, 18, 28]))
        for i in range(rng.randint(1, 6))
    ]
    state = rng.choice(sorted(STATE_CODES))
    coupon = FakeCoupon(kind="flat", value=rng.randint(0, 50_000)) if seed % 2 else None
    quote = build_quote(lines, state, coupon, NOW)

    assert sum(line.taxable_paise for line in quote.lines) == quote.taxable_paise
    assert quote.taxable_paise == quote.subtotal_paise - quote.discount_paise
    assert quote.total_paise == quote.taxable_paise + quote.tax_paise + quote.shipping_paise
    if state == "MH":
        assert quote.igst_paise == 0
    else:
        assert quote.cgst_paise == quote.sgst_paise == 0
