"""Daily logs, surplus estimates and the model registry (plan 4.11)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import EstimateMethod, LogSource, QuantityUnit

Quantity = Field(ge=0, le=1_000_000, max_digits=10, decimal_places=2)


class DailyLogIn(BaseModel):
    log_date: date
    food_category_id: uuid.UUID
    unit: QuantityUnit
    produced_qty: Decimal = Quantity
    sold_qty: Decimal = Quantity  # surplus = max(0, produced - sold) is calculated by the server


class DailyLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    log_date: date
    food_category_id: uuid.UUID
    unit: QuantityUnit
    produced_qty: Decimal
    sold_qty: Decimal
    surplus_qty: Decimal
    source: LogSource
    updated_at: datetime


class ImportResult(BaseModel):
    created: int
    updated: int


class EstimateIn(BaseModel):
    food_category_id: uuid.UUID
    target_date: date
    planned_production: Decimal | None = Field(default=None, gt=0, le=1_000_000, max_digits=10, decimal_places=2)


class EstimateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    food_category_id: uuid.UUID
    food_category_name: str | None
    target_date: date
    unit: QuantityUnit
    predicted_qty: Decimal  # shown as "about 12 (8-15)"
    lower_qty: Decimal
    upper_qty: Decimal
    method: EstimateMethod
    confidence: str  # normal / low
    model_version: str | None
    planned_production: Decimal | None
    actual_qty: Decimal | None  # filled in once that day is logged
    explanation: str
    note: str  # "This is an estimate ..., not a guarantee ... nothing about food safety"
    created_at: datetime


class MlModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    version: str
    algorithm: str
    data_source: str  # synthetic / real / mixed - shown as "Trained on synthetic data"
    training_rows: int
    metrics: dict[str, Any]
    notes: str | None
    is_active: bool
    trained_at: datetime
    activated_at: datetime | None
