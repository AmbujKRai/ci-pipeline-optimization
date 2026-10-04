from datetime import datetime

import pytest
from pydantic import ValidationError

from app.clock import to_naive_utc, utcnow
from app.schemas import CouponCreate, OrderCreate, PaymentRequest, ProductCreate, UserCreate
from app.services.notifications import is_valid_email


@pytest.mark.parametrize(
    "email",
    ["a@b.co", "first.last@example.com", "user+tag@sub.example.in", "x_y-z@ex-ample.org"],
)
def test_valid_emails(email):
    assert is_valid_email(email)


@pytest.mark.parametrize(
    "email",
    [
        "",
        "plain",
        "@example.com",
        "a@",
        "a@b",
        "a@b.c",
        "a..b@example.com",
        "a b@example.com",
        "a@example..com",
    ],
)
def test_invalid_emails(email):
    assert not is_valid_email(email)


def test_registration_normalises_email_and_name():
    user = UserCreate(
        email="  Asha@Example.COM ", full_name="  Asha   Patil ", password="Passw0rd!"
    )
    assert user.email == "asha@example.com"
    assert user.full_name == "Asha Patil"


@pytest.mark.parametrize("sku", ["ABC", "ELEC-EARBUDS-01", "A1B2C3"])
def test_valid_skus(sku):
    ProductCreate(sku=sku, name="Thing", category="home", price="10.00")


@pytest.mark.parametrize("sku", ["AB", "-ABC", "abc-lower", "SPACE SKU", "X" * 33])
def test_invalid_skus(sku):
    with pytest.raises(ValidationError):
        ProductCreate(sku=sku, name="Thing", category="home", price="10.00")


@pytest.mark.parametrize("price", ["0", "-1.00", "10.999", "1000000.01"])
def test_invalid_prices(price):
    with pytest.raises(ValidationError):
        ProductCreate(sku="ABC", name="Thing", category="home", price=price)


def test_unknown_category_is_rejected_by_the_api_model():
    with pytest.raises(ValidationError):
        ProductCreate(sku="ABC", name="Thing", category="toys", price="10.00")


@pytest.mark.parametrize("value", ["95", "12.5", "0"])
def test_percent_coupon_needs_a_whole_number_up_to_90(value):
    with pytest.raises(ValidationError):
        CouponCreate(code="SAVE", kind="percent", value=value)


def test_flat_coupon_cannot_cap_its_discount():
    with pytest.raises(ValidationError, match="max_discount"):
        CouponCreate(code="FLAT50", kind="flat", value="50", max_discount="10")


def test_valid_coupon_model():
    coupon = CouponCreate(code="SAVE10", kind="percent", value="10", max_discount="200")
    assert coupon.min_order == 0


@pytest.mark.parametrize("token", ["visa", "tok_", "tok_VISA", "tok_" + "a" * 41, "tok_visa!"])
def test_invalid_card_tokens(token):
    with pytest.raises(ValidationError):
        PaymentRequest(card_token=token)


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "shipping_state": "MH"},
        {"items": [{"product_id": 1, "quantity": 0}], "shipping_state": "MH"},
        {"items": [{"product_id": 0, "quantity": 1}], "shipping_state": "MH"},
        {"items": [{"product_id": 1, "quantity": 1}], "shipping_state": "MAH"},
        {"items": [{"product_id": i, "quantity": 1} for i in range(1, 52)], "shipping_state": "MH"},
    ],
)
def test_invalid_order_payloads(payload):
    with pytest.raises(ValidationError):
        OrderCreate(**payload)


def test_to_naive_utc_converts_aware_datetimes():
    aware = datetime.fromisoformat("2026-05-01T10:00:00+05:30")
    assert to_naive_utc(aware) == datetime(2026, 5, 1, 4, 30)


def test_to_naive_utc_keeps_naive_datetimes():
    naive = datetime(2026, 5, 1, 4, 30)
    assert to_naive_utc(naive) is naive


def test_utcnow_is_naive():
    assert utcnow().tzinfo is None
