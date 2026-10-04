"""Money helpers. Amounts are handled as integer paise; rupees only appear at the edges."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE_PER_RUPEE = 100
_TWO_PLACES = Decimal("0.01")
_WHOLE = Decimal("1")


def round_rupees(amount: Decimal) -> Decimal:
    """Round a rupee amount to two decimal places, half-up (the usual billing rule)."""
    return amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def to_paise(rupees: Decimal | int | str) -> int:
    """Convert rupees to paise, e.g. Decimal("499.99") -> 49999."""
    try:
        value = Decimal(str(rupees))
    except InvalidOperation:
        raise ValueError(f"not a valid amount: {rupees!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a valid amount: {rupees!r}")
    return int(round_rupees(value) * PAISE_PER_RUPEE)


def from_paise(paise: int) -> Decimal:
    """Convert paise to rupees with exactly two decimal places."""
    return (Decimal(paise) / PAISE_PER_RUPEE).quantize(_TWO_PLACES)


def percent_of(paise: int, percent: Decimal | int) -> int:
    """Return `percent` % of an amount in paise, rounded half-up to the nearest paisa."""
    value = Decimal(paise) * Decimal(percent) / 100
    return int(value.quantize(_WHOLE, rounding=ROUND_HALF_UP))


def _group_indian(digits: str) -> str:
    """Group digits the Indian way: last three, then pairs (12,34,56,789)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join([*pairs, tail])


def format_inr(paise: int) -> str:
    """Format paise as rupees with Indian digit grouping, e.g. 12345678 -> "₹1,23,456.78"."""
    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), PAISE_PER_RUPEE)
    return f"{sign}₹{_group_indian(str(rupees))}.{remainder:02d}"
