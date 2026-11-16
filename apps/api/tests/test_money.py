"""Money helpers shared by the generator and the engines."""

import pytest

from revlane.money import bps_of, from_decimal_string, to_decimal_string


@pytest.mark.parametrize(
    ("amount", "rate", "expected"),
    [
        (1_050_000, 192, 20_160),
        (1_000_000, 215, 21_500),
        (33_333, 186, 620),  # 619.99 rounds up
        (25, 200, 1),  # exactly half rounds up
        (24, 200, 0),
        (0, 200, 0),
        (-33_333, 186, -620),
        (-25, 200, -1),
    ],
)
def test_bps_of_rounds_half_away_from_zero(amount: int, rate: int, expected: int) -> None:
    assert bps_of(amount, rate) == expected


@pytest.mark.parametrize(
    ("minor", "text"),
    [(1_050_000, "10500.00"), (5, "0.05"), (0, "0.00"), (-62_050, "-620.50")],
)
def test_decimal_strings_round_trip(minor: int, text: str) -> None:
    assert to_decimal_string(minor) == text
    assert from_decimal_string(text) == minor


def test_parsing_accepts_what_providers_write() -> None:
    assert from_decimal_string("10500") == 1_050_000
    assert from_decimal_string(" 10500.5 ") == 1_050_050


@pytest.mark.parametrize("bad", ["10500.005", "ten", ""])
def test_parsing_refuses_bad_amounts(bad: str) -> None:
    with pytest.raises(ValueError):
        from_decimal_string(bad)
