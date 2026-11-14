"""Model registry and surplus estimates (plan 7.2 and 7.7).

- train_and_register: train on the given data, save the model file, record it in ml_models and make it
  the active model ONLY if it beats both baselines and the current model on the same hold-out days.
- estimate: the number shown when a provider adds a listing. It is always an estimate, never a promise,
  and never about food safety. With too little history it falls back to a simple rule and says so."""

import csv
import logging
import statistics
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import COUNTABLE_UNITS, EstimateMethod, LogSource, QuantityUnit
from app.ml import calendar_pk
from app.ml.surplus_features import FEATURE_VERSION, Day, build
from app.ml.surplus_training import Dataset, Series, evaluate, predict, train
from app.models.category import FoodCategory
from app.models.ml import MlModel, ProviderDailyLog, SurplusPrediction
from app.models.user import User
from app.services.audit_service import ClientInfo, record

log = logging.getLogger("freshlink.ml")
MODEL_NAME = "surplus"
OWN_HISTORY_DAYS = 60  # history the cold-start rule looks at
_cache: dict[str, dict[str, Any]] = {}  # version -> loaded bundle (loaded once per process)

ESTIMATE_NOTE = ("This is an estimate to help you plan, not a guarantee. Always enter the real quantity you have. "
                 "It says nothing about whether food is safe to eat.")


# ============================================================== training data


def series_from_db(db: Session) -> tuple[list[Series], str]:
    """Every provider/category history in the database, plus where the data came from."""
    rows = db.execute(select(ProviderDailyLog, FoodCategory.perishability_class).join(
        FoodCategory, FoodCategory.id == ProviderDailyLog.food_category_id)).all()
    groups: dict[tuple, dict[date, Day]] = defaultdict(dict)
    meta, sources = {}, set()
    for log_row, perishability in rows:
        key = (log_row.provider_id, log_row.food_category_id, log_row.unit)
        groups[key][log_row.log_date] = Day(float(log_row.produced_qty), float(log_row.sold_qty),
                                            float(log_row.surplus_qty))
        meta[key] = perishability.value
        sources.add("synthetic" if log_row.source == LogSource.SYNTHETIC else "real")
    series = [Series(f"{k[0]}/{k[1]}", meta[k], k[2].value, h) for k, h in groups.items()]
    return series, _source_label(sources)


def series_from_csv(path: Path) -> tuple[list[Series], str]:
    """The synthetic generator's file (ml/data/synthetic_daily_logs.csv)."""
    groups: dict[tuple, dict[date, Day]] = defaultdict(dict)
    meta, sources = {}, set()
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["provider_key"], r["food_category_code"], r["unit"])
            groups[key][date.fromisoformat(r["date"])] = Day(float(r["produced"]), float(r["sold"]), float(r["surplus"]))
            meta[key] = r["perishability"]
            sources.add(r.get("source") or "real")
    return [Series(f"{k[0]}/{k[1]}", meta[k], k[2], h) for k, h in groups.items()], _source_label(sources)


def _source_label(sources: set[str]) -> str:
    return "mixed" if len(sources) > 1 else (next(iter(sources)) if sources else "none")


# ============================================================== registry


def _load(artifacts: Path, model: MlModel) -> dict[str, Any]:
    if model.version not in _cache:
        bundle = joblib.load(artifacts / model.artifact)
        if bundle.get("feature_version") != FEATURE_VERSION:
            raise RuntimeError(f"Model {model.version} was trained with other features; retrain it.")
        if bundle.get("sklearn_version") != sklearn.__version__:
            log.warning("Model %s was saved with scikit-learn %s, running %s", model.version,
                        bundle.get("sklearn_version"), sklearn.__version__)
        _cache[model.version] = bundle
    return _cache[model.version]


def active_model(db: Session) -> MlModel | None:
    return db.scalar(select(MlModel).where(MlModel.name == MODEL_NAME, MlModel.is_active.is_(True)))


def train_and_register(db: Session, artifacts: Path, series: list[Series], data_source: str,
                       notes: str | None = None) -> MlModel:
    result = train(series)
    now = datetime.now(UTC)
    version = f"{MODEL_NAME}-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"  # suffix: two trainings in one second
    artifacts.mkdir(parents=True, exist_ok=True)
    joblib.dump(result.bundle, artifacts / f"{version}.joblib")

    metrics = dict(result.metrics)
    current = active_model(db)
    reasons = []
    beats_baselines = (metrics["model"]["mae"] < metrics["baseline_b2"]["mae"]
                       and metrics["model"]["mae"] < metrics["baseline_b1"]["mae"])
    if not beats_baselines:
        reasons.append("does not beat the simple baselines")
    if current is not None:
        try:  # the current model, measured on the SAME hold-out days, for a fair comparison
            current_mae = evaluate(_load(artifacts, current), result.holdout)["model"]["mae"]
            metrics["current_model_on_this_holdout"] = {"version": current.version, "mae": current_mae}
            if metrics["model"]["mae"] > current_mae:
                reasons.append(f"not better than the active model {current.version} ({current_mae} MAE)")
        except Exception as exc:  # noqa: BLE001 - a missing / broken old file must not block a new model
            metrics["current_model_on_this_holdout"] = {"version": current.version, "error": str(exc)[:200]}
    activate = not reasons
    metrics["activation"] = "activated" if activate else "kept inactive: " + "; ".join(reasons)

    row = MlModel(name=MODEL_NAME, version=version, algorithm=result.algorithm, artifact=f"{version}.joblib",
                  data_source=data_source, training_rows=result.training_rows, metrics=metrics, notes=notes)
    if activate:
        db.execute(update(MlModel).where(MlModel.name == MODEL_NAME, MlModel.is_active.is_(True)).values(is_active=False))
        db.flush()
        row.is_active, row.activated_at = True, now
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_models(db: Session) -> list[MlModel]:
    return list(db.scalars(select(MlModel).order_by(MlModel.trained_at.desc())))


def activate(db: Session, admin: User, model_id: uuid.UUID, artifacts: Path, client: ClientInfo) -> MlModel:
    """Roll forward or back to any registered model whose file is still present."""
    model = db.scalar(select(MlModel).where(MlModel.id == model_id).with_for_update(of=MlModel))
    if model is None:
        raise AppError(404, "NOT_FOUND", "Model not found.")
    if not (artifacts / model.artifact).exists():
        raise AppError(409, "MODEL_FILE_MISSING", "This model's file is no longer on the server. Retrain instead.")
    previous = active_model(db)
    if previous is not None and previous.id != model.id:
        previous.is_active = False
        db.flush()
    model.is_active, model.activated_at = True, datetime.now(UTC)
    record(db, actor=admin, action="ml_model.activate", entity_type="ml_model", entity_id=model.id, client=client,
           before={"active": previous.version if previous else None}, after={"active": model.version})
    db.commit()
    db.refresh(model)
    return model


# ============================================================== estimates


def _round(value: float, unit: QuantityUnit) -> Decimal:
    places = Decimal("1") if unit in COUNTABLE_UNITS else Decimal("0.1")
    return Decimal(str(max(value, 0.0))).quantize(places, rounding=ROUND_HALF_UP)


def _history(db: Session, provider_id: uuid.UUID, category_id: uuid.UUID, since: date) -> tuple[dict[date, Day], QuantityUnit | None]:
    logs = db.scalars(select(ProviderDailyLog).where(
        ProviderDailyLog.provider_id == provider_id, ProviderDailyLog.food_category_id == category_id,
        ProviderDailyLog.log_date >= since).order_by(ProviderDailyLog.log_date)).all()
    unit = logs[-1].unit if logs else None
    return ({lg.log_date: Day(float(lg.produced_qty), float(lg.sold_qty), float(lg.surplus_qty)) for lg in logs},
            unit)


def estimate(db: Session, provider_id: uuid.UUID, category: FoodCategory, target: date,
             planned_production: Decimal | None, artifacts: Path) -> SurplusPrediction:
    history, unit = _history(db, provider_id, category.id, target - timedelta(days=OWN_HISTORY_DAYS))
    model_row = active_model(db)
    features = build(history, target, category.perishability_class.value,
                     float(planned_production) if planned_production is not None else None)
    method, version, confidence = EstimateMethod.MODEL, None, "normal"

    if features is not None and model_row is not None:
        bundle = _load(artifacts, model_row)
        data = Dataset(np.asarray([features.values]), np.zeros(1), np.asarray([features.scale]), np.zeros(1),
                       np.zeros(1), np.zeros(1), np.asarray([category.perishability_class.value]))
        mid, low, high = (float(a[0]) for a in predict(bundle, data))
        version = model_row.version
    elif history:  # cold start: the provider's own same-weekday average (baseline B2)
        method, confidence = EstimateMethod.OWN_AVERAGE, "low"
        same = [history[d].surplus for d in (target - timedelta(days=7 * k) for k in range(1, 5)) if d in history]
        values = same or [d.surplus for d in history.values()]
        mid = statistics.fmean(values)
        spread = statistics.pstdev(values) if len(values) > 1 else mid * 0.5
        low, high = mid - spread, mid + spread
    else:  # no history at all: the median of other providers in this category (same unit)
        method, confidence = EstimateMethod.CATEGORY_MEDIAN, "low"
        others = db.execute(select(ProviderDailyLog.surplus_qty, ProviderDailyLog.unit).where(
            ProviderDailyLog.food_category_id == category.id,
            ProviderDailyLog.log_date >= target - timedelta(days=OWN_HISTORY_DAYS))).all()
        if not others:
            raise AppError(422, "NO_HISTORY", "There isn't enough data yet to estimate this category. "
                           "Record a few days of production and sales first (daily logs).")
        counts: dict[QuantityUnit, list[float]] = defaultdict(list)
        for qty, u in others:
            counts[u].append(float(qty))
        unit, values = max(counts.items(), key=lambda kv: len(kv[1]))
        values.sort()
        mid = statistics.median(values)
        low, high = values[len(values) // 10], values[(len(values) * 9) // 10]

    unit = unit or QuantityUnit.KG
    prediction = SurplusPrediction(
        provider_id=provider_id, food_category_id=category.id, target_date=target, unit=unit,
        predicted_qty=_round(mid, unit), lower_qty=_round(min(low, mid), unit), upper_qty=_round(max(high, mid), unit),
        method=method, confidence=confidence, model_version=version, planned_production=planned_production)
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return decorate(db, [prediction])[0]


def decorate(db: Session, predictions: list[SurplusPrediction]) -> list[SurplusPrediction]:
    """Adds the actual surplus (once the provider has logged that day) and the explanation."""
    keys = {(p.provider_id, p.food_category_id, p.target_date) for p in predictions}
    actual = {}
    if keys:
        for lg in db.scalars(select(ProviderDailyLog).where(
                ProviderDailyLog.provider_id.in_({k[0] for k in keys}),
                ProviderDailyLog.log_date.in_({k[2] for k in keys}))):
            actual[(lg.provider_id, lg.food_category_id, lg.log_date)] = lg.surplus_qty
    names = dict(db.execute(select(FoodCategory.id, FoodCategory.name)).all())
    for p in predictions:
        p.actual_qty = actual.get((p.provider_id, p.food_category_id, p.target_date))
        p.food_category_name = names.get(p.food_category_id)
        p.note = ESTIMATE_NOTE
        p.explanation = {
            EstimateMethod.MODEL: "Estimated by the FreshLink model from your recent daily logs, the weekday and "
                                  "calendar effects (Ramadan, Eid, holidays).",
            EstimateMethod.OWN_AVERAGE: "Low confidence - based on limited history: the average of your recent days. "
                                        "Log at least 2 weeks of production and sales for a better estimate.",
            EstimateMethod.CATEGORY_MEDIAN: "Low confidence - you have no logs for this category yet, so this is "
                                            "the typical surplus of other providers. Start logging for a personal estimate.",
        }[p.method]
        if not calendar_pk.covered(p.target_date):
            p.explanation += " (Ramadan and Eid dates for this year are not in the calendar yet.)"
    return predictions

