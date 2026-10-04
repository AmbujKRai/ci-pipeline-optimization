"""E-mail notification tests against the stub mail service (about 250 ms per message)."""

import pytest

from app.models import Order, OrderItem, User
from app.services.notifications import (
    StubEmailNotifier,
    cancellation_notice,
    order_confirmation,
    shipment_notice,
)
from tests.conftest import scaled_ms

pytestmark = pytest.mark.external

ROUND_TRIP_MS = 250


@pytest.fixture
def notifier() -> StubEmailNotifier:
    return StubEmailNotifier(latency_ms=scaled_ms(ROUND_TRIP_MS))


@pytest.fixture
def order() -> Order:
    return Order(
        id=42,
        subtotal_paise=250_000,
        discount_paise=25_000,
        cgst_paise=20_250,
        sgst_paise=20_250,
        igst_paise=0,
        shipping_paise=0,
        total_paise=265_500,
        items=[OrderItem(quantity=2), OrderItem(quantity=1)],
    )


@pytest.fixture
def user() -> User:
    return User(email="asha@example.com", full_name="Asha Patil")


def test_sending_records_the_message(notifier):
    message_id = notifier.send("asha@example.com", "Hello", "Body")
    assert message_id.startswith("msg_")
    assert notifier.outbox[0].subject == "Hello"


@pytest.mark.parametrize("recipient", ["", "asha", "asha@", "a b@example.com"])
def test_invalid_recipients_are_refused(notifier, recipient):
    with pytest.raises(ValueError, match="recipient"):
        notifier.send(recipient, "Hello", "Body")
    assert notifier.outbox == []


def test_empty_subject_is_refused(notifier):
    with pytest.raises(ValueError, match="subject"):
        notifier.send("asha@example.com", "   ", "Body")


def test_message_ids_are_unique(notifier):
    ids = {notifier.send("asha@example.com", f"Update {n}", "Body") for n in range(3)}
    assert len(ids) == 3


def test_sent_to_filters_by_recipient(notifier):
    notifier.send("asha@example.com", "One", "Body")
    notifier.send("rohan@example.com", "Two", "Body")
    notifier.send("asha@example.com", "Three", "Body")
    assert [m.subject for m in notifier.sent_to("asha@example.com")] == ["One", "Three"]


def test_order_confirmation(notifier, order, user):
    subject, body = order_confirmation(order, user)
    notifier.send(user.email, subject, body)
    assert subject == "ShopLite order #42 confirmed"
    assert "Hi Asha Patil" in body
    assert "Items:    3" in body
    assert "₹2,655.00" in body
    assert "₹405.00" in body  # CGST + SGST


def test_shipment_notice(notifier, order, user):
    subject, body = shipment_notice(order, user)
    notifier.send(user.email, subject, body)
    assert subject == "ShopLite order #42 shipped"
    assert "on its way" in body


@pytest.mark.parametrize(
    ("refunded", "phrase"),
    [(True, "A refund of ₹2,655.00 has been issued."), (False, "No payment was taken.")],
)
def test_cancellation_notice(notifier, order, user, refunded, phrase):
    subject, body = cancellation_notice(order, user, refunded)
    notifier.send(user.email, subject, body)
    assert subject == "ShopLite order #42 cancelled"
    assert phrase in body
