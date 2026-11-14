"""Provider daily logs: what was produced and sold each day (plan 4.11). The training data for
surplus estimation. A provider sees and changes only their own logs.

Rules:
- one log per provider, day and food category: recording the same day again updates it;
- surplus = max(0, produced - sold) is calculated here (the database checks it too);
- a food category keeps one unit for a provider (mixing kg and packages would make the history meaningless);
- no future days and nothing older than a year;
- a CSV import is all-or-nothing: one bad row and nothing is saved, with the row numbers to fix."""

import csv
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import COUNTABLE_UNITS, LogSource, QuantityUnit
from app.models.category import FoodCategory
from app.models.ml import ProviderDailyLog
from app.models.profiles import ProviderProfile
from app.schemas.ml import DailyLogIn

MAX_AGE_DAYS = 365
MAX_IMPORT_ROWS = 3000
IMPORT_COLUMNS = ["date", "food_category_code", "unit", "produced", "sold"]


def _check(db: Session, provider: ProviderProfile, item: DailyLogIn, today: date,
           units: dict[uuid.UUID, QuantityUnit]) -> str | None:
    """Problem with one log, or None. `units` caches the unit already used per category."""
    if item.log_date > today:
        return "The date is in the future."
    if item.log_date < today - timedelta(days=MAX_AGE_DAYS):
        return f"The date is more than {MAX_AGE_DAYS} days ago."
    if item.sold_qty > item.produced_qty * 2 + 1000:
        return "Sold is far more than produced; please check the numbers."
    if item.unit in COUNTABLE_UNITS and (item.produced_qty % 1 or item.sold_qty % 1):
        return f"{item.unit.value} is counted in whole numbers."
    if item.food_category_id not in units:
        units[item.food_category_id] = db.scalar(select(ProviderDailyLog.unit).where(
            ProviderDailyLog.provider_id == provider.id, ProviderDailyLog.food_category_id == item.food_category_id)
            .limit(1)) or item.unit
    if units[item.food_category_id] != item.unit:
        return f"This category is logged in {units[item.food_category_id].value}; keep the same unit."
    return None


def _save(db: Session, provider: ProviderProfile, item: DailyLogIn, source: LogSource) -> bool:
    """Insert or update; True when a new row was created."""
    row = db.scalar(select(ProviderDailyLog).where(
        ProviderDailyLog.provider_id == provider.id, ProviderDailyLog.log_date == item.log_date,
        ProviderDailyLog.food_category_id == item.food_category_id).with_for_update(of=ProviderDailyLog))
    created = row is None
    if created:
        row = ProviderDailyLog(provider_id=provider.id, log_date=item.log_date, food_category_id=item.food_category_id)
        db.add(row)
    row.unit, row.produced_qty, row.sold_qty = item.unit, item.produced_qty, item.sold_qty
    row.surplus_qty = max(item.produced_qty - item.sold_qty, Decimal(0))
    row.source = source
    return created


def _active_category(db: Session, category_id: uuid.UUID) -> FoodCategory:
    category = db.get(FoodCategory, category_id)
    if category is None or not category.is_active:
        raise AppError(422, "VALIDATION_ERROR", "Please choose an active food category.",
                       {"fields": [{"field": "body.food_category_id", "message": "Unknown or inactive category."}]})
    return category


def record(db: Session, provider: ProviderProfile, item: DailyLogIn, today: date) -> tuple[ProviderDailyLog, bool]:
    _active_category(db, item.food_category_id)
    problem = _check(db, provider, item, today, {})
    if problem:
        raise AppError(422, "INVALID_DAILY_LOG", problem)
    created = _save(db, provider, item, LogSource.MANUAL)
    db.commit()
    row = db.scalar(select(ProviderDailyLog).where(
        ProviderDailyLog.provider_id == provider.id, ProviderDailyLog.log_date == item.log_date,
        ProviderDailyLog.food_category_id == item.food_category_id))
    return row, created


def list_logs(db: Session, provider: ProviderProfile, *, food_category_id: uuid.UUID | None, date_from: date | None,
              date_to: date | None, page: int, page_size: int) -> dict[str, Any]:
    query = select(ProviderDailyLog).where(ProviderDailyLog.provider_id == provider.id)
    if food_category_id:
        query = query.where(ProviderDailyLog.food_category_id == food_category_id)
    if date_from:
        query = query.where(ProviderDailyLog.log_date >= date_from)
    if date_to:
        query = query.where(ProviderDailyLog.log_date <= date_to)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(db.scalars(query.order_by(ProviderDailyLog.log_date.desc(), ProviderDailyLog.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def delete_log(db: Session, provider: ProviderProfile, log_id: uuid.UUID) -> None:
    row = db.get(ProviderDailyLog, log_id)
    if row is None or row.provider_id != provider.id:
        raise AppError(404, "NOT_FOUND", "Daily log not found.")
    db.delete(row)
    db.commit()


def import_csv(db: Session, provider: ProviderProfile, content: bytes, today: date) -> dict[str, int]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError(422, "INVALID_FILE", "The file must be a UTF-8 CSV file.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if [c.strip().lower() for c in (reader.fieldnames or [])] != IMPORT_COLUMNS:
        raise AppError(422, "INVALID_FILE", "The first line must be: " + ",".join(IMPORT_COLUMNS),
                       {"expected_columns": IMPORT_COLUMNS})
    categories = {c.code: c for c in db.scalars(select(FoodCategory).where(FoodCategory.is_active.is_(True)))}
    items, errors, seen = [], [], set()
    units: dict[uuid.UUID, QuantityUnit] = {}
    for line, raw in enumerate(reader, start=2):
        if line - 1 > MAX_IMPORT_ROWS:
            raise AppError(422, "INVALID_FILE", f"At most {MAX_IMPORT_ROWS} rows per file.")
        try:
            category = categories.get((raw["food_category_code"] or "").strip())
            if category is None:
                raise ValueError(f"unknown food category code {raw['food_category_code']!r}")
            item = DailyLogIn(log_date=date.fromisoformat(raw["date"].strip()), food_category_id=category.id,
                              unit=QuantityUnit(raw["unit"].strip()), produced_qty=Decimal(raw["produced"].strip()),
                              sold_qty=Decimal(raw["sold"].strip()))
        except (ValueError, InvalidOperation, AttributeError, KeyError) as exc:
            errors.append({"row": line, "message": str(exc).splitlines()[0][:150]})
            continue
        key = (item.log_date, item.food_category_id)
        problem = "the same day and category appears twice in the file" if key in seen else \
            _check(db, provider, item, today, units)
        seen.add(key)
        if problem:
            errors.append({"row": line, "message": problem})
        else:
            items.append(item)
    if errors:
        raise AppError(422, "INVALID_FILE", f"{len(errors)} row(s) have problems; nothing was imported.",
                       {"errors": errors[:20]})
    if not items:
        raise AppError(422, "INVALID_FILE", "The file has no rows.")
    created = sum(_save(db, provider, item, LogSource.IMPORT) for item in items)
    db.commit()
    return {"created": created, "updated": len(items) - created}
