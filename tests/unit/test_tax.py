from typing import get_args

import pytest

from app.schemas import Category
from app.services.tax import (
    CATEGORIES,
    STATE_CODES,
    InvalidStateError,
    TaxBreakdown,
    UnknownCategoryError,
    compute_gst,
    gst_rate,
    is_intra_state,
    normalise_state,
)

CATEGORY_RATES = [
    ("essentials", 5),
    ("books", 5),
    ("apparel", 12),
    ("footwear", 12),
    ("electronics", 18),
    ("home", 18),
    ("beauty", 18),
    ("luxury", 28),
]


@pytest.mark.parametrize(("category", "rate"), CATEGORY_RATES)
def test_gst_rate_per_category(category, rate):
    assert gst_rate(category) == rate


@pytest.mark.parametrize("category", ["toys", "", "ELECTRONICS", "food"])
def test_unknown_category_is_rejected(category):
    with pytest.raises(UnknownCategoryError):
        gst_rate(category)


def test_api_category_list_matches_tax_table():
    assert set(get_args(Category)) == set(CATEGORIES)


def test_all_states_and_union_territories_are_known():
    assert len(STATE_CODES) == 36


@pytest.mark.parametrize(("raw", "clean"), [("mh", "MH"), (" ka ", "KA"), ("Dl", "DL")])
def test_normalise_state(raw, clean):
    assert normalise_state(raw) == clean


@pytest.mark.parametrize("bad", ["XX", "", "MAH", "M", "12"])
def test_invalid_state_is_rejected(bad):
    with pytest.raises(InvalidStateError):
        normalise_state(bad)


def test_intra_state_detection():
    assert is_intra_state("mh")
    assert not is_intra_state("KA")


@pytest.mark.parametrize("state", sorted(STATE_CODES - {"MH"}))
def test_shipments_to_other_states_pay_igst(state):
    assert compute_gst(100_000, 18, state) == TaxBreakdown(igst_paise=18_000)


@pytest.mark.parametrize(("category", "rate"), CATEGORY_RATES)
def test_shipments_within_maharashtra_split_cgst_and_sgst(category, rate):
    tax = compute_gst(100_000, gst_rate(category), "MH")
    assert tax.cgst_paise == tax.sgst_paise == 100_000 * rate // 200
    assert tax.igst_paise == 0
    assert tax.total_paise == 1_000 * rate


def test_split_rounding_can_differ_from_igst_by_a_paisa():
    # 101 paise at 5 %: CGST = SGST = 2.525 -> 3 each (6 total), IGST = 5.05 -> 5.
    assert compute_gst(101, 5, "MH").total_paise == 6
    assert compute_gst(101, 5, "KA").total_paise == 5


def test_zero_rate_is_allowed():
    assert compute_gst(5_000, 0, "KA") == TaxBreakdown()


@pytest.mark.parametrize("rate", [3, 10, 30, -5])
def test_unsupported_rate_is_rejected(rate):
    with pytest.raises(ValueError, match="unsupported GST rate"):
        compute_gst(1_000, rate, "KA")


def test_negative_taxable_amount_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        compute_gst(-1, 18, "MH")


def test_seller_state_can_be_changed():
    assert compute_gst(10_000, 18, "KA", seller_state="KA") == TaxBreakdown(900, 900, 0)
    assert compute_gst(10_000, 18, "MH", seller_state="KA") == TaxBreakdown(igst_paise=1_800)
