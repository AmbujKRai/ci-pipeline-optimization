"""GST calculation (simplified Indian Goods and Services Tax).

Within the seller's state the tax is split equally into CGST and SGST; across states the
full rate is charged as IGST. Each component is rounded half-up to the nearest paisa.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.money import percent_of

SELLER_STATE = "MH"  # ShopLite ships from Mumbai, Maharashtra.

# Simplified GST slabs per product category (percent).
GST_RATES: dict[str, int] = {
    "essentials": 5,
    "books": 5,
    "apparel": 12,
    "footwear": 12,
    "electronics": 18,
    "home": 18,
    "beauty": 18,
    "luxury": 28,
}
CATEGORIES: tuple[str, ...] = tuple(GST_RATES)
VALID_RATES: frozenset[int] = frozenset({0, *GST_RATES.values()})

# Two-letter codes for the 28 states and 8 union territories.
STATE_CODES: frozenset[str] = frozenset(
    {
        "AP", "AR", "AS", "BR", "CT", "GA", "GJ", "HR", "HP", "JH", "KA", "KL", "MP", "MH",
        "MN", "ML", "MZ", "NL", "OR", "PB", "RJ", "SK", "TN", "TG", "TR", "UP", "UT", "WB",
        "AN", "CH", "DN", "DL", "JK", "LA", "LD", "PY",
    }
)  # fmt: skip


class UnknownCategoryError(ValueError):
    """Raised for a product category that has no GST slab."""


class InvalidStateError(ValueError):
    """Raised for a shipping state code that is not an Indian state or union territory."""


@dataclass(frozen=True)
class TaxBreakdown:
    cgst_paise: int = 0
    sgst_paise: int = 0
    igst_paise: int = 0

    @property
    def total_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise


def gst_rate(category: str) -> int:
    try:
        return GST_RATES[category]
    except KeyError:
        raise UnknownCategoryError(f"unknown category: {category!r}") from None


def normalise_state(code: str) -> str:
    cleaned = code.strip().upper()
    if cleaned not in STATE_CODES:
        raise InvalidStateError(f"unknown state code: {code!r}")
    return cleaned


def is_intra_state(buyer_state: str, seller_state: str = SELLER_STATE) -> bool:
    return normalise_state(buyer_state) == normalise_state(seller_state)


def compute_gst(
    taxable_paise: int, rate: int, buyer_state: str, seller_state: str = SELLER_STATE
) -> TaxBreakdown:
    """Tax due on `taxable_paise` at `rate` percent for a shipment to `buyer_state`."""
    if taxable_paise < 0:
        raise ValueError("taxable amount cannot be negative")
    if rate not in VALID_RATES:
        raise ValueError(f"unsupported GST rate: {rate}")
    if is_intra_state(buyer_state, seller_state):
        half = percent_of(taxable_paise, Decimal(rate) / 2)
        return TaxBreakdown(cgst_paise=half, sgst_paise=half)
    return TaxBreakdown(igst_paise=percent_of(taxable_paise, rate))
