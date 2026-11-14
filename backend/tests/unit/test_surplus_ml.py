"""Stage 10a: the surplus-estimation ML code without a database - features, calendar, the synthetic
generator and training. The key property: features never use information from the last 7 days."""

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.ml import calendar_pk
from app.ml.surplus_features import FEATURE_NAMES, GAP, Day, build
from app.ml.surplus_training import Series, build_dataset, predict, train

GENERATOR = Path(__file__).resolve().parents[3] / "ml" / "data" / "generate.py"


def _generator():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("freshlink_generate", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["freshlink_generate"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def synthetic() -> list[Series]:
    """A slice of the synthetic data: 8 provider/category histories (fast to train)."""
    rows = _generator().generate()
    keep = {"P01", "P02", "P13", "P14", "P23", "P31"}
    groups: dict = {}
    for r in rows:
        if r["provider_key"] in keep:
            key = (r["provider_key"], r["food_category_code"], r["perishability"], r["unit"])
            groups.setdefault(key, {})[date.fromisoformat(r["date"])] = Day(r["produced"], r["sold"], r["surplus"])
    return [Series("/".join(k[:2]), k[2], k[3], h) for k, h in groups.items()]


def _flat(n: int, start: date = date(2026, 9, 1), produced: float = 100, sold: float = 80) -> dict[date, Day]:
    return {start + timedelta(days=i): Day(produced, sold, produced - sold) for i in range(n)}


# ===================================================================== features


def test_feature_row_shape_and_baselines() -> None:
    row = build(_flat(60), date(2026, 10, 25), "bakery")
    assert len(row.values) == len(FEATURE_NAMES) == 30
    assert (row.scale, row.b1, row.b2) == (100, 20, 20)
    named = dict(zip(FEATURE_NAMES, row.values, strict=True))
    assert named["lag_7"] == 0.2 and named["sell_through_28"] == 0.8 and named["type_bakery"] == 1.0
    assert named["has_planned"] == 0.0 and build(_flat(60), date(2026, 10, 25), "bakery", 150).values[
        FEATURE_NAMES.index("planned_vs_usual")] == 1.5


def test_too_little_history_gives_none() -> None:
    assert build({}, date(2026, 10, 1), "bakery") is None
    assert build(_flat(10), date(2026, 9, 20), "bakery") is None  # fewer than 14 days in the window
    assert build(_flat(60, produced=0, sold=0), date(2026, 10, 25), "bakery") is None  # nothing produced


# Fixed at 6 on purpose (NOT taken from GAP): the promise to users is "estimates up to a week ahead",
# so the 6 days before the target must never matter, whatever the code's settings say.
LAST_WEEK = 6


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(min_value=0, max_value=500), min_size=LAST_WEEK, max_size=LAST_WEEK))
def test_no_peeking_at_the_last_week(recent: list[float]) -> None:
    """Whatever happens in the 6 days right before the target, the features are identical:
    the model can be used up to a week ahead and training can't leak the future."""
    target = date(2026, 10, 25)
    history = _flat(60, start=date(2026, 8, 20))
    changed = dict(history)
    for i, value in enumerate(recent, start=1):
        changed[target - timedelta(days=i)] = Day(value + 50, 50, value)
    changed[target] = Day(999, 0, 999)  # even the target day itself
    assert build(changed, target, "dairy").values == build(history, target, "dairy").values


def test_calendar() -> None:
    assert calendar_pk.is_ramadan(date(2026, 3, 1)) and not calendar_pk.is_ramadan(date(2026, 3, 21))
    assert calendar_pk.is_eid(date(2026, 3, 21)) and calendar_pk.is_eid(date(2026, 3, 23))
    assert not calendar_pk.is_eid(date(2026, 3, 24))
    assert calendar_pk.is_holiday(date(2026, 8, 14)) and not calendar_pk.is_holiday(date(2026, 8, 13))
    assert calendar_pk.days_until_eid(date(2026, 5, 25)) == 2 and calendar_pk.days_until_eid(date(2026, 7, 1)) == 14
    assert calendar_pk.covered(date(2027, 1, 1)) and not calendar_pk.covered(date(2031, 1, 1))


# ============================================================ data and training


def test_generator_is_reproducible_and_labelled() -> None:
    gen = _generator()
    first, second = gen.generate()[:500], gen.generate()[:500]
    assert first == second  # fixed seed: the same data every time
    assert all(r["source"] == "synthetic" for r in first)
    assert all(r["surplus"] == round(r["produced"] - r["sold"], 2) >= 0 for r in first)


def test_training_beats_the_baselines(synthetic: list[Series]) -> None:
    result = train(synthetic)
    m = result.metrics
    assert set(m["cross_validation_mae"]) == {"Ridge", "RandomForestRegressor", "HistGradientBoostingRegressor"}
    assert m["chosen"] == result.algorithm
    assert m["model"]["mae"] < m["baseline_b2"]["mae"] < m["baseline_b1"]["mae"]
    assert 60 <= m["range_coverage_pct"] <= 100
    start, end = (date.fromisoformat(d) for d in m["holdout_period"])
    assert (end - start).days == 59  # the last 60 days are never trained on
    held = [date.fromordinal(int(d)) for d in result.holdout.dates]
    assert min(held) >= start and max(held) == end  # the hold-out really is that final period, not a random mix

    mid, low, high = predict(result.bundle, result.holdout)
    assert np.all(mid >= 0) and np.all(low <= mid) and np.all(mid <= high)


def test_training_refuses_thin_data() -> None:
    with pytest.raises(ValueError, match="Not enough history"):
        train([Series("x", "bakery", "package", _flat(60))])


def test_dataset_only_uses_days_with_history(synthetic: list[Series]) -> None:
    one = synthetic[:1]
    data = build_dataset(one)
    first_day = min(one[0].history)
    assert date.fromordinal(int(data.dates.min())) >= first_day + timedelta(days=GAP + 13)
