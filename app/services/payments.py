"""Payment gateway interface and an in-process stub.

The stub behaves like a hosted card gateway: every call costs one network round trip
(`latency_ms`), outcomes are decided by well-known test card tokens, and charges are
idempotent per key.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

TRANSACTION_LIMIT_PAISE = 2_00_000_00  # ₹2,00,000 per transaction

# Test card tokens -> decline reason (None means the charge succeeds).
TEST_CARD_OUTCOMES: dict[str, str | None] = {
    "tok_visa": None,
    "tok_mastercard": None,
    "tok_rupay": None,
    "tok_upi": None,
    "tok_flaky": None,  # times out once per idempotency key, then succeeds
    "tok_declined": "card_declined",
    "tok_insufficient_funds": "insufficient_funds",
    "tok_expired": "expired_card",
    "tok_fraud": "suspected_fraud",
}
# Tokens that simulate network trouble instead of a card outcome.
ALWAYS_TIMES_OUT = frozenset({"tok_timeout"})
TIMES_OUT_ONCE = frozenset({"tok_flaky"})


class GatewayTimeoutError(Exception):
    """The gateway did not answer in time. Safe to retry with the same idempotency key."""


class PaymentDeclinedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(f"payment declined: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class PaymentResult:
    approved: bool
    reference: str | None = None
    decline_reason: str | None = None


class PaymentGateway(Protocol):
    def charge(self, amount_paise: int, card_token: str, idempotency_key: str) -> PaymentResult: ...

    def refund(self, reference: str, amount_paise: int) -> str: ...


def _reference(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()
    return f"{prefix}_{digest[:16]}"


class StubPaymentGateway:
    def __init__(self, latency_ms: int = 0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.latency_ms = latency_ms
        self._sleep = sleep
        self._results: dict[str, PaymentResult] = {}
        self._captured: dict[str, int] = {}
        self._refunded: dict[str, int] = {}
        self._flaky_seen: set[str] = set()
        self.calls = 0

    def _round_trip(self) -> None:
        self.calls += 1
        if self.latency_ms:
            self._sleep(self.latency_ms / 1000)

    def charge(self, amount_paise: int, card_token: str, idempotency_key: str) -> PaymentResult:
        self._round_trip()
        if idempotency_key in self._results:
            return self._results[idempotency_key]
        if amount_paise <= 0:
            raise ValueError("amount must be positive")
        if card_token in ALWAYS_TIMES_OUT:
            raise GatewayTimeoutError("gateway timed out")
        if card_token in TIMES_OUT_ONCE and idempotency_key not in self._flaky_seen:
            self._flaky_seen.add(idempotency_key)
            raise GatewayTimeoutError("gateway timed out")

        if card_token not in TEST_CARD_OUTCOMES:
            result = PaymentResult(approved=False, decline_reason="invalid_token")
        elif (reason := TEST_CARD_OUTCOMES[card_token]) is not None:
            result = PaymentResult(approved=False, decline_reason=reason)
        elif amount_paise > TRANSACTION_LIMIT_PAISE:
            result = PaymentResult(approved=False, decline_reason="limit_exceeded")
        else:
            reference = _reference("pay", idempotency_key, amount_paise)
            self._captured[reference] = amount_paise
            result = PaymentResult(approved=True, reference=reference)
        self._results[idempotency_key] = result
        return result

    def refund(self, reference: str, amount_paise: int) -> str:
        self._round_trip()
        captured = self._captured.get(reference)
        if captured is None:
            raise ValueError(f"unknown payment reference: {reference}")
        already = self._refunded.get(reference, 0)
        if amount_paise <= 0 or already + amount_paise > captured:
            raise ValueError("refund exceeds the captured amount")
        self._refunded[reference] = already + amount_paise
        return _reference("rfnd", reference, already + amount_paise)

    def refunded_amount(self, reference: str) -> int:
        return self._refunded.get(reference, 0)


def charge_with_retry(
    gateway: PaymentGateway,
    amount_paise: int,
    card_token: str,
    idempotency_key: str,
    attempts: int = 3,
    backoff_seconds: float = 0.05,
    sleep: Callable[[float], None] = time.sleep,
) -> PaymentResult:
    """Charge, retrying timeouts with exponential backoff. The idempotency key prevents
    a retry from charging the customer twice."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(1, attempts + 1):
        try:
            return gateway.charge(amount_paise, card_token, idempotency_key)
        except GatewayTimeoutError:
            if attempt == attempts:
                raise
            sleep(backoff_seconds * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # pragma: no cover
