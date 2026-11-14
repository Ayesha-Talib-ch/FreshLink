"""Features for surplus estimation (plan 7.2). ONE module used by training and by the API, so the
model always sees features computed exactly the same way (no "train/serve skew").

Two design choices worth explaining in the viva:

1. Every history feature ends 7 days BEFORE the target day (the "gap"). So the same model can
   estimate any day up to a week after the provider's latest log, and training can never peek at
   information it wouldn't have at prediction time (no future leakage).

2. Everything is divided by the provider's usual production ("scale"). A bakery making 300 packages
   and a dhaba making 20 kg are then on the same footing, so one model learns from all providers.
   The prediction is multiplied back by the scale."""

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from app.ml import calendar_pk

GAP = 7  # history features end GAP days before the target: estimates up to GAP days ahead
WINDOW = 28  # days of history used for the averages
MIN_DAYS_IN_WINDOW = 14  # fewer logged days than this in the window = not enough history for the model
FEATURE_VERSION = 1  # bump when the features change: old model files then refuse to load

PERISHABILITY = ("bakery", "cooked", "fresh_produce", "dairy", "packaged", "other")


@dataclass(frozen=True)
class Day:
    produced: float
    sold: float
    surplus: float


@dataclass(frozen=True)
class FeatureRow:
    values: list[float]  # in FEATURE_NAMES order
    scale: float  # usual daily production; the model predicts surplus / scale
    b1: float  # baseline 1: surplus on the same weekday last week
    b2: float  # baseline 2: average surplus of the same weekday over the last 4 weeks


FEATURE_NAMES = [
    *(f"dow_{i}" for i in range(7)), "is_weekend", "month_sin", "month_cos",
    "is_ramadan", "is_eid", "is_holiday", "days_until_eid",
    "lag_7", "lag_14", "same_weekday_mean_4", "rolling_mean_7", "rolling_mean_28", "rolling_std_7",
    "sell_through_28", "planned_vs_usual", "has_planned", "log_scale",
    *(f"type_{p}" for p in PERISHABILITY),
]


def _window(history: dict[date, Day], end: date, days: int) -> list[Day]:
    return [history[d] for d in (end - timedelta(days=i) for i in range(days)) if d in history]


def build(history: dict[date, Day], target: date, perishability: str,
          planned_production: float | None = None) -> FeatureRow | None:
    """Features for estimating the surplus on `target`. None when the history is too thin
    (the API then uses a simple, clearly labelled fallback instead of the model)."""
    end = target - timedelta(days=GAP)  # the newest day the features may use
    w28 = _window(history, end, WINDOW)
    if len(w28) < MIN_DAYS_IN_WINDOW:
        return None
    scale = statistics.fmean(d.produced for d in w28)
    if scale <= 0:
        return None
    w7 = _window(history, end, 7) or w28[:7]
    rm28 = statistics.fmean(d.surplus for d in w28)
    rm7 = statistics.fmean(d.surplus for d in w7)
    rstd7 = statistics.pstdev([d.surplus for d in w7]) if len(w7) > 1 else 0.0
    lag_7 = history[target - timedelta(days=7)].surplus if target - timedelta(days=7) in history else rm7
    lag_14 = history[target - timedelta(days=14)].surplus if target - timedelta(days=14) in history else rm28
    same_weekday = [history[d].surplus for d in (target - timedelta(days=7 * k) for k in range(1, 5)) if d in history]
    b2 = statistics.fmean(same_weekday) if same_weekday else rm28
    produced = sum(d.produced for d in w28)
    sell_through = sum(min(d.sold, d.produced) for d in w28) / produced if produced else 0.0
    usual_production = statistics.fmean(d.produced for d in w7)

    dow = target.weekday()
    values = [
        *(1.0 if dow == i else 0.0 for i in range(7)),
        1.0 if dow >= 5 else 0.0,  # Saturday / Sunday
        math.sin(2 * math.pi * target.month / 12), math.cos(2 * math.pi * target.month / 12),
        float(calendar_pk.is_ramadan(target)), float(calendar_pk.is_eid(target)), float(calendar_pk.is_holiday(target)),
        calendar_pk.days_until_eid(target) / 14,
        lag_7 / scale, lag_14 / scale, b2 / scale, rm7 / scale, rm28 / scale, rstd7 / scale,
        sell_through,
        (planned_production if planned_production is not None else usual_production) / scale,
        1.0 if planned_production is not None else 0.0,
        math.log1p(scale),
        *(1.0 if perishability == p else 0.0 for p in PERISHABILITY),
    ]
    return FeatureRow(values=values, scale=scale, b1=lag_7, b2=b2)
