"""Pricing engine: bulk discounts, coupons, discount allocation, shipping and the final quote."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.clock import utcnow
from app.services.money import percent_of
from app.services.tax import SELLER_STATE, TaxBreakdown, compute_gst, normalise_state

# (minimum quantity, discount percent), checked from the largest tier down.
BULK_TIERS: tuple[tuple[int, int], ...] = ((50, 10), (20, 7), (10, 5))
FREE_SHIPPING_THRESHOLD_PAISE = 99_900  # ₹999
STANDARD_SHIPPING_PAISE = 4_900  # ₹49
MAX_LINE_QUANTITY = 1_000


class CouponError(ValueError):
    """A coupon cannot be applied. `reason` is a short machine-readable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CouponTerms(Protocol):
    """The coupon fields the pricing engine needs (satisfied by the ORM model)."""

    code: str
    kind: str
    value: int
    max_discount_paise: int | None
    min_order_paise: int
    expires_at: datetime | None
    usage_limit: int | None
    used_count: int
    is_active: bool


@dataclass(frozen=True)
class CartLine:
    product_id: int
    unit_price_paise: int
    quantity: int
    tax_rate: int


@dataclass(frozen=True)
class PricedLine:
    product_id: int
    quantity: int
    unit_price_paise: int
    bulk_discount_percent: int
    line_total_paise: int
    coupon_share_paise: int
    taxable_paise: int
    tax_rate: int
    tax: TaxBreakdown


@dataclass(frozen=True)
class Quote:
    lines: tuple[PricedLine, ...]
    subtotal_paise: int
    discount_paise: int
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    shipping_paise: int
    total_paise: int

    @property
    def tax_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise


def bulk_discount_percent(quantity: int) -> int:
    for minimum, percent in BULK_TIERS:
        if quantity >= minimum:
            return percent
    return 0


def line_total_paise(unit_price_paise: int, quantity: int) -> int:
    """Price of a cart line after its bulk discount."""
    if quantity < 1 or quantity > MAX_LINE_QUANTITY:
        raise ValueError(f"quantity must be between 1 and {MAX_LINE_QUANTITY}")
    if unit_price_paise < 0:
        raise ValueError("unit price cannot be negative")
    gross = unit_price_paise * quantity
    return gross - percent_of(gross, bulk_discount_percent(quantity))


def validate_coupon(coupon: CouponTerms, subtotal_paise: int, now: datetime) -> None:
    if not coupon.is_active:
        raise CouponError("inactive")
    if coupon.expires_at is not None and now >= coupon.expires_at:
        raise CouponError("expired")
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise CouponError("usage_limit_reached")
    if subtotal_paise < coupon.min_order_paise:
        raise CouponError("minimum_not_met")


def coupon_discount_paise(coupon: CouponTerms, subtotal_paise: int) -> int:
    """Discount a valid coupon gives on `subtotal_paise`. Never more than the subtotal."""
    if coupon.kind == "percent":
        discount = percent_of(subtotal_paise, coupon.value)
        if coupon.max_discount_paise is not None:
            discount = min(discount, coupon.max_discount_paise)
    elif coupon.kind == "flat":
        discount = coupon.value
    else:
        raise CouponError("unknown_kind")
    return max(0, min(discount, subtotal_paise))


def allocate(total: int, weights: Sequence[int]) -> list[int]:
    """Split `total` across `weights` proportionally (largest remainder method).

    The parts always add up to exactly `total`; ties go to the earlier position.
    """
    if total < 0:
        raise ValueError("total cannot be negative")
    if any(weight < 0 for weight in weights):
        raise ValueError("weights cannot be negative")
    weight_sum = sum(weights)
    if weight_sum == 0:
        if total:
            raise ValueError("cannot allocate a non-zero total over zero weights")
        return [0] * len(weights)
    shares = [total * weight // weight_sum for weight in weights]
    remainders = [total * weight % weight_sum for weight in weights]
    leftover = total - sum(shares)
    by_remainder = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for index in by_remainder[:leftover]:
        shares[index] += 1
    return shares


def shipping_fee_paise(order_value_paise: int) -> int:
    """Flat shipping fee, waived at or above the free-shipping threshold."""
    if order_value_paise <= 0:
        return 0
    if order_value_paise >= FREE_SHIPPING_THRESHOLD_PAISE:
        return 0
    return STANDARD_SHIPPING_PAISE


def build_quote(
    lines: Sequence[CartLine],
    buyer_state: str,
    coupon: CouponTerms | None = None,
    now: datetime | None = None,
    seller_state: str = SELLER_STATE,
) -> Quote:
    """Price a cart: bulk discounts, then the coupon, then GST per line, then shipping."""
    if not lines:
        raise ValueError("cart is empty")
    buyer_state = normalise_state(buyer_state)

    line_totals = [line_total_paise(line.unit_price_paise, line.quantity) for line in lines]
    subtotal = sum(line_totals)

    discount = 0
    if coupon is not None:
        validate_coupon(coupon, subtotal, now or utcnow())
        discount = coupon_discount_paise(coupon, subtotal)

    # The coupon reduces each line's taxable value in proportion to its share of the cart.
    shares = allocate(discount, line_totals)
    priced: list[PricedLine] = []
    for line, total, share in zip(lines, line_totals, shares, strict=True):
        taxable = total - share
        priced.append(
            PricedLine(
                product_id=line.product_id,
                quantity=line.quantity,
                unit_price_paise=line.unit_price_paise,
                bulk_discount_percent=bulk_discount_percent(line.quantity),
                line_total_paise=total,
                coupon_share_paise=share,
                taxable_paise=taxable,
                tax_rate=line.tax_rate,
                tax=compute_gst(taxable, line.tax_rate, buyer_state, seller_state),
            )
        )

    taxable_total = subtotal - discount
    cgst = sum(line.tax.cgst_paise for line in priced)
    sgst = sum(line.tax.sgst_paise for line in priced)
    igst = sum(line.tax.igst_paise for line in priced)
    shipping = shipping_fee_paise(taxable_total)
    return Quote(
        lines=tuple(priced),
        subtotal_paise=subtotal,
        discount_paise=discount,
        taxable_paise=taxable_total,
        cgst_paise=cgst,
        sgst_paise=sgst,
        igst_paise=igst,
        shipping_paise=shipping,
        total_paise=taxable_total + cgst + sgst + igst + shipping,
    )
