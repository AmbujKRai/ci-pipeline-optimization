"""Time helpers. The database stores naive UTC datetimes, so everything is normalised to that."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current time as a naive UTC datetime."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Convert an aware datetime to naive UTC; naive values are assumed to be UTC already."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
