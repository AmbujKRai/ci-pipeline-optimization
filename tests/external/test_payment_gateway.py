"""Payment gateway contract tests.

The stub emulates a hosted card gateway, including a network round trip per call
(about 400 ms, scaled by TEST_LATENCY_SCALE), so these tests are I/O-bound like real
integration tests against a sandbox gateway.
"""

import pytest

from app.services.payments import (
    TRANSACTION_LIMIT_PAISE,
    GatewayTimeoutError,
    StubPaymentGateway,
    charge_with_retry,
)
from tests.conftest import scaled_ms

pytestmark = pytest.mark.external

ROUND_TRIP_MS = 400


@pytest.fixture
def gateway() -> StubPaymentGateway:
    return StubPaymentGateway(latency_ms=scaled_ms(ROUND_TRIP_MS))


@pytest.mark.parametrize("token", ["tok_visa", "tok_mastercard", "tok_rupay", "tok_upi"])
def test_successful_charges(gateway, token):
    result = gateway.charge(150_000, token, f"order-1:{token}")
    assert result.approved
    assert result.reference.startswith("pay_")
    assert result.decline_reason is None


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("tok_declined", "card_declined"),
        ("tok_insufficient_funds", "insufficient_funds"),
        ("tok_expired", "expired_card"),
        ("tok_fraud", "suspected_fraud"),
        ("tok_unknown", "invalid_token"),
    ],
)
def test_declines(gateway, token, reason):
    result = gateway.charge(150_000, token, f"order-2:{token}")
    assert not result.approved
    assert result.reference is None
    assert result.decline_reason == reason


def test_amount_over_the_limit_is_declined(gateway):
    result = gateway.charge(TRANSACTION_LIMIT_PAISE + 1, "tok_visa", "big")
    assert result.decline_reason == "limit_exceeded"


def test_amount_at_the_limit_is_approved(gateway):
    assert gateway.charge(TRANSACTION_LIMIT_PAISE, "tok_visa", "limit").approved


def test_non_positive_amounts_are_rejected(gateway):
    with pytest.raises(ValueError):
        gateway.charge(0, "tok_visa", "zero")


def test_same_idempotency_key_returns_the_same_payment(gateway):
    first = gateway.charge(99_900, "tok_visa", "order-7:tok_visa")
    second = gateway.charge(99_900, "tok_visa", "order-7:tok_visa")
    assert first == second
    assert gateway.calls == 2


def test_different_keys_create_different_payments(gateway):
    first = gateway.charge(99_900, "tok_visa", "order-8:a")
    second = gateway.charge(99_900, "tok_visa", "order-8:b")
    assert first.reference != second.reference


def test_timeouts_surface_as_errors(gateway):
    with pytest.raises(GatewayTimeoutError):
        gateway.charge(10_000, "tok_timeout", "slow")


def test_retry_recovers_from_a_flaky_network(gateway):
    result = charge_with_retry(gateway, 10_000, "tok_flaky", "flaky-1", backoff_seconds=0.01)
    assert result.approved
    assert gateway.calls == 2


def test_retry_backs_off_exponentially_then_gives_up(gateway):
    pauses: list[float] = []
    with pytest.raises(GatewayTimeoutError):
        charge_with_retry(
            gateway,
            10_000,
            "tok_timeout",
            "down",
            attempts=3,
            backoff_seconds=0.05,
            sleep=pauses.append,
        )
    assert gateway.calls == 3
    assert pauses == [0.05, 0.1]


def test_retry_needs_at_least_one_attempt(gateway):
    with pytest.raises(ValueError):
        charge_with_retry(gateway, 10_000, "tok_visa", "never", attempts=0)


def test_full_refund(gateway):
    payment = gateway.charge(50_000, "tok_visa", "refund-full")
    refund_id = gateway.refund(payment.reference, 50_000)
    assert refund_id.startswith("rfnd_")
    assert gateway.refunded_amount(payment.reference) == 50_000


def test_partial_refunds_add_up(gateway):
    payment = gateway.charge(50_000, "tok_visa", "refund-partial")
    gateway.refund(payment.reference, 20_000)
    gateway.refund(payment.reference, 30_000)
    assert gateway.refunded_amount(payment.reference) == 50_000


def test_cannot_refund_more_than_was_captured(gateway):
    payment = gateway.charge(50_000, "tok_visa", "refund-over")
    gateway.refund(payment.reference, 40_000)
    with pytest.raises(ValueError, match="exceeds"):
        gateway.refund(payment.reference, 20_000)


def test_unknown_payment_cannot_be_refunded(gateway):
    with pytest.raises(ValueError, match="unknown payment"):
        gateway.refund("pay_doesnotexist", 1_000)


def test_declined_charge_cannot_be_refunded(gateway):
    declined = gateway.charge(50_000, "tok_declined", "refund-declined")
    assert declined.reference is None
    with pytest.raises(ValueError):
        gateway.refund("pay_none", 50_000)
