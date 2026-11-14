"""The fairness measures: hand-checked examples plus properties over thousands of random cases."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.engines.fairness import fill_ratio, gini

# Real quantities: stored as NUMERIC(10,2), so 0 or at least 0.01, at most 99 999 999.99.
# (Plain floats would include values like 5e-324, which no listing can ever hold.)
quantity = st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False)
amounts = st.lists(quantity, min_size=1, max_size=60)


def test_hand_checked_examples() -> None:
    assert gini([10, 10, 10]) == 0.0  # everyone got the same
    assert gini([0, 0, 30]) == 0.6667  # one of three got everything: the maximum, 1 - 1/3
    assert gini([15, 10, 5]) == 0.2222  # the spec 5.5 example (NGO 15, kitchen 10, family 5)
    assert gini([]) is None and gini([0, 0]) is None  # nothing to compare


def test_negative_amounts_are_refused() -> None:
    with pytest.raises(ValueError):
        gini([5, -1])


@given(amounts)
def test_gini_is_between_zero_and_its_maximum(values: list[Decimal]) -> None:
    g = gini(values)
    if g is not None:
        assert 0 <= g <= 1 - 1 / len(values) + 1e-4


@given(amounts, st.decimals(min_value="0.01", max_value=1000, places=2))
def test_gini_ignores_the_unit(values: list[Decimal], factor: Decimal) -> None:
    """Measuring in grams instead of kg doesn't change fairness."""
    g, scaled = gini(values), gini([v * factor for v in values])
    assert (g is None) == (scaled is None)
    if g is not None and scaled is not None:
        assert abs(g - scaled) < 1e-3


@given(amounts)
def test_gini_ignores_the_order(values: list[Decimal]) -> None:
    assert gini(values) == gini(list(reversed(values)))


@given(st.integers(min_value=1, max_value=50), quantity.filter(lambda q: q > 0))
def test_equal_shares_are_perfectly_fair(n: int, amount: Decimal) -> None:
    assert gini([amount] * n) == 0.0


def test_fill_ratio() -> None:
    assert fill_ratio(10, 10) == 1.0 and fill_ratio(10, 5) == 0.5 and fill_ratio(10, 0) == 0.0
    assert fill_ratio(10, 12) == 1.0  # never above 1
    with pytest.raises(ValueError):
        fill_ratio(0, 0)
