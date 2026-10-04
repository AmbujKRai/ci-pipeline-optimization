from decimal import Decimal

import pytest

from app.services.money import format_inr, from_paise, percent_of, round_rupees, to_paise


@pytest.mark.parametrize(
    ("rupees", "paise"),
    [
        ("0", 0),
        ("0.01", 1),
        ("1", 100),
        ("499.99", 49_999),
        ("1000000", 100_000_000),
        ("10.005", 1_001),  # half-up
        ("10.004", 1_000),
        (Decimal("2.675"), 268),
        (2.675, 268),  # floats go through str(), so 2.675 is not 2.67499999...
        (5, 500),
        ("-3.50", -350),
    ],
)
def test_to_paise(rupees, paise):
    assert to_paise(rupees) == paise


@pytest.mark.parametrize("garbage", ["abc", "", "NaN", "Infinity", "1,000"])
def test_to_paise_rejects_garbage(garbage):
    with pytest.raises(ValueError):
        to_paise(garbage)


@pytest.mark.parametrize(
    ("paise", "rupees"),
    [(0, "0.00"), (1, "0.01"), (100, "1.00"), (49_999, "499.99"), (-250, "-2.50")],
)
def test_from_paise(paise, rupees):
    assert from_paise(paise) == Decimal(rupees)
    assert str(from_paise(paise)) == rupees


@pytest.mark.parametrize("paise", [0, 1, 99, 12_345, 9_999_999])
def test_paise_round_trip(paise):
    assert to_paise(from_paise(paise)) == paise


@pytest.mark.parametrize(
    ("amount", "rounded"),
    [("1.234", "1.23"), ("1.235", "1.24"), ("1.2", "1.20"), ("-1.235", "-1.24")],
)
def test_round_rupees_half_up(amount, rounded):
    assert round_rupees(Decimal(amount)) == Decimal(rounded)


@pytest.mark.parametrize(
    ("paise", "percent", "expected"),
    [
        (10_000, 18, 1_800),
        (10_000, 5, 500),
        (999, 18, 180),  # 179.82
        (333, 9, 30),  # 29.97
        (50, 9, 5),  # 4.5 rounds up
        (150, 3, 5),  # 4.5 rounds up
        (0, 28, 0),
        (12_345, Decimal("2.5"), 309),  # 308.625
        (12_345, 9, 1_111),  # 1111.05
    ],
)
def test_percent_of(paise, percent, expected):
    assert percent_of(paise, percent) == expected


@pytest.mark.parametrize(
    ("paise", "text"),
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (99_900, "₹999.00"),
        (100_000, "₹1,000.00"),
        (1_234_567, "₹12,345.67"),
        (12_345_678, "₹1,23,456.78"),
        (1_000_000_000, "₹1,00,00,000.00"),
        (8_999_900, "₹89,999.00"),
        (-123_456, "-₹1,234.56"),
    ],
)
def test_format_inr_uses_indian_grouping(paise, text):
    assert format_inr(paise) == text
