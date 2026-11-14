"""Machine-learning data (plan 3.3 and 7.2): provider daily logs, the model registry and
surplus predictions.

provider_daily_logs is the training data for surplus estimation: what a provider produced and
sold each day. surplus = max(0, produced - sold) is calculated by the server, never typed in."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import EstimateMethod, LogSource, QuantityUnit
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class ProviderDailyLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_daily_logs"
    __table_args__ = (
        UniqueConstraint("provider_id", "log_date", "food_category_id", name="one_log_per_day_and_category"),
        CheckConstraint("produced_qty >= 0 AND sold_qty >= 0 AND surplus_qty >= 0", name="quantities_not_negative"),
        CheckConstraint("surplus_qty = GREATEST(produced_qty - sold_qty, 0)", name="surplus_is_produced_minus_sold"),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provider_profiles.id", ondelete="CASCADE"), index=True)
    log_date: Mapped[date] = mapped_column(Date)
    food_category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_categories.id", ondelete="RESTRICT"))
    unit: Mapped[QuantityUnit] = mapped_column(str_enum(QuantityUnit, "log_quantity_unit"))
    produced_qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    sold_qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    surplus_qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    source: Mapped[LogSource] = mapped_column(str_enum(LogSource, "log_source"), server_default=LogSource.MANUAL.value)


class MlModel(UUIDPrimaryKeyMixin, Base):
    """Registry of trained models (plan 7.7). At most one active model per name."""

    __tablename__ = "ml_models"
    __table_args__ = (
        Index("uq_ml_models_one_active", "name", unique=True, postgresql_where=text("is_active")),
        UniqueConstraint("name", "version", name="name_version"),
    )

    name: Mapped[str] = mapped_column(String(40))  # e.g. "surplus"
    version: Mapped[str] = mapped_column(String(60))  # e.g. "surplus-20261115-1530"
    algorithm: Mapped[str] = mapped_column(String(60))  # e.g. "HistGradientBoostingRegressor"
    artifact: Mapped[str] = mapped_column(String(200))  # file name inside the artifacts folder
    data_source: Mapped[str] = mapped_column(String(20))  # synthetic / real / mixed
    training_rows: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSONB)  # hold-out MAE, RMSE, R2, baselines, coverage, per category
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=false())
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SurplusPrediction(UUIDPrimaryKeyMixin, Base):
    """Every estimate given to a provider, so its accuracy can be measured once the day is logged."""

    __tablename__ = "surplus_predictions"
    __table_args__ = (Index("ix_surplus_predictions_provider_created", "provider_id", text("created_at DESC")),)

    provider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provider_profiles.id", ondelete="CASCADE"))
    food_category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_categories.id", ondelete="RESTRICT"))
    target_date: Mapped[date] = mapped_column(Date)
    unit: Mapped[QuantityUnit] = mapped_column(str_enum(QuantityUnit, "prediction_quantity_unit"))
    predicted_qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    lower_qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    upper_qty: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    method: Mapped[EstimateMethod] = mapped_column(str_enum(EstimateMethod, "estimate_method"))
    confidence: Mapped[str] = mapped_column(String(10))  # normal / low
    model_version: Mapped[str | None] = mapped_column(String(60))
    planned_production: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
