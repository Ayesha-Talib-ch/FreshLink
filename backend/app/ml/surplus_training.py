"""Training and evaluation of the surplus model (plan 7.2). Used by the CLI, the weekly worker job
and the ml/ scripts, so every model is trained and measured the same way.

Method, in plain words:
1. Turn every logged day (with enough history before it) into one row of features -> target.
2. Keep the LAST 60 DAYS aside as the final test ("hold-out"). The model never sees them.
3. On the rest, compare Ridge regression, Random Forest and Gradient Boosting with TimeSeriesSplit
   (always train on the past, test on the following weeks - never shuffled) and pick the best by MAE.
4. Train the winner on all training days and measure it on the hold-out against two simple
   baselines it must beat: B1 = same weekday last week, B2 = same-weekday average of 4 weeks.
5. Train two quantile models (10 % and 90 %) for the "about 12 (8-15)" range and check how often
   the real value falls inside it on the hold-out (coverage, ideally about 80 %)."""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.surplus_features import FEATURE_NAMES, FEATURE_VERSION, Day, build

HOLDOUT_DAYS = 60
CV_SPLITS = 4
MIN_TRAINING_ROWS = 500  # below this the comparison isn't meaningful (the worker job skips)


@dataclass
class Series:
    key: str  # e.g. "provider-id/category-code"
    perishability: str
    unit: str
    history: dict[date, Day]


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray  # surplus / scale
    scale: np.ndarray
    b1: np.ndarray
    b2: np.ndarray
    dates: np.ndarray  # target dates (as ordinal numbers)
    perishability: np.ndarray

    def take(self, mask: np.ndarray) -> "Dataset":
        return Dataset(*(getattr(self, f)[mask] for f in ("X", "y", "scale", "b1", "b2", "dates", "perishability")))

    @property
    def actual(self) -> np.ndarray:
        return self.y * self.scale


def build_dataset(series: Iterable[Series]) -> Dataset:
    rows, y, scale, b1, b2, dates, kinds = [], [], [], [], [], [], []
    for s in series:
        for day in sorted(s.history):
            f = build(s.history, day, s.perishability)  # no planned production: it isn't logged
            if f is None:
                continue
            rows.append(f.values)
            y.append(s.history[day].surplus / f.scale)
            scale.append(f.scale)
            b1.append(f.b1)
            b2.append(f.b2)
            dates.append(day.toordinal())
            kinds.append(s.perishability)
    return Dataset(np.asarray(rows, dtype=float).reshape(-1, len(FEATURE_NAMES)), np.asarray(y), np.asarray(scale),
                   np.asarray(b1), np.asarray(b2), np.asarray(dates), np.asarray(kinds))


def _candidates() -> dict[str, Any]:
    return {
        "Ridge": make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "RandomForestRegressor": RandomForestRegressor(n_estimators=120, min_samples_leaf=5, max_features=0.5,
                                                       n_jobs=2, random_state=0),
        "HistGradientBoostingRegressor": HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05,
                                                                       random_state=0),
    }


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    err = predicted - actual
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    return {"mae": round(float(np.mean(np.abs(err))), 3), "rmse": round(float(math.sqrt(np.mean(err ** 2))), 3),
            "r2": round(1 - float(np.sum(err ** 2)) / ss_tot, 3) if ss_tot > 0 else 0.0}


def predict(bundle: dict[str, Any], data: Dataset) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(estimate, low, high) in real units, never below zero, low <= estimate <= high."""
    mid = np.clip(bundle["model"].predict(data.X), 0, None) * data.scale
    low = np.clip(bundle["q_low"].predict(data.X), 0, None) * data.scale
    high = np.clip(bundle["q_high"].predict(data.X), 0, None) * data.scale
    return mid, np.minimum(low, mid), np.maximum(high, mid)


def evaluate(bundle: dict[str, Any], test: Dataset) -> dict[str, Any]:
    mid, low, high = predict(bundle, test)
    actual = test.actual
    model, b1, b2 = _metrics(actual, mid), _metrics(actual, test.b1), _metrics(actual, test.b2)
    per_type = {}
    for kind in sorted(set(test.perishability)):
        m = test.perishability == kind
        per_type[kind] = {"rows": int(m.sum()), "model_mae": _metrics(actual[m], mid[m])["mae"],
                          "b2_mae": _metrics(actual[m], test.b2[m])["mae"]}
    return {
        "holdout_rows": int(len(actual)), "model": model, "baseline_b1": b1, "baseline_b2": b2,
        "improvement_over_b2_pct": round(100 * (b2["mae"] - model["mae"]) / b2["mae"], 1) if b2["mae"] else 0.0,
        "range_coverage_pct": round(100 * float(np.mean((actual >= low - 1e-9) & (actual <= high + 1e-9))), 1),
        "per_food_type": per_type,
    }


@dataclass
class TrainResult:
    bundle: dict[str, Any]
    algorithm: str
    training_rows: int
    metrics: dict[str, Any]
    holdout: Dataset = field(repr=False)


def train(series: list[Series]) -> TrainResult:
    data = build_dataset(series)
    if len(data.y) < MIN_TRAINING_ROWS:
        raise ValueError(f"Not enough history to train: {len(data.y)} usable days (need {MIN_TRAINING_ROWS}).")
    cutoff = int(data.dates.max()) - HOLDOUT_DAYS
    train_set, test = data.take(data.dates <= cutoff), data.take(data.dates > cutoff)

    # model selection: TimeSeriesSplit over whole days, so a fold never mixes past and future
    order = np.argsort(train_set.dates, kind="stable")
    ordered = train_set.take(order)
    days = np.unique(ordered.dates)
    cv = {}
    for name, model in _candidates().items():
        maes = []
        for fit_days, val_days in TimeSeriesSplit(n_splits=CV_SPLITS).split(days):
            fit = np.isin(ordered.dates, days[fit_days])
            val = np.isin(ordered.dates, days[val_days])
            model.fit(ordered.X[fit], ordered.y[fit])
            pred = np.clip(model.predict(ordered.X[val]), 0, None) * ordered.scale[val]
            maes.append(float(np.mean(np.abs(pred - ordered.y[val] * ordered.scale[val]))))
        cv[name] = round(float(np.mean(maes)), 3)
    best = min(cv, key=cv.get)

    model = _candidates()[best].fit(train_set.X, train_set.y)
    q_low = HistGradientBoostingRegressor(loss="quantile", quantile=0.1, max_iter=200, learning_rate=0.05,
                                          random_state=0).fit(train_set.X, train_set.y)
    q_high = HistGradientBoostingRegressor(loss="quantile", quantile=0.9, max_iter=200, learning_rate=0.05,
                                           random_state=0).fit(train_set.X, train_set.y)
    bundle = {"model": model, "q_low": q_low, "q_high": q_high, "features": FEATURE_NAMES,
              "feature_version": FEATURE_VERSION, "sklearn_version": sklearn.__version__, "algorithm": best}
    metrics = evaluate(bundle, test)
    metrics["cross_validation_mae"] = cv
    metrics["chosen"] = best
    metrics["holdout_period"] = [date.fromordinal(cutoff + 1).isoformat(), date.fromordinal(int(data.dates.max())).isoformat()]
    return TrainResult(bundle=bundle, algorithm=best, training_rows=int(len(train_set.y)), metrics=metrics, holdout=test)

