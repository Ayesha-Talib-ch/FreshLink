"""Admin-managed categories (Technical Plan section 3.3, "Configuration").

The spec requires configurable categories (section 2.3: "configurable receiver
categories rather than permanently restricting receivers to only NGOs"), so
they are rows in tables, not hard-coded lists. The starting rows are inserted
by migration 0004; admin screens to edit them come in the admin stage.

`code` is a fixed identifier used by the program (e.g. "small_business");
`name` is the label users see and admins may rename.
"""

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, Text, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import PerishabilityClass
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class _CategoryColumns(UUIDPrimaryKeyMixin, TimestampMixin):
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class ProviderCategory(_CategoryColumns, Base):
    """Bakery, Restaurant, Fast-food shop, Grocery store / Supermarket."""

    __tablename__ = "provider_categories"


class ReceiverCategory(_CategoryColumns, Base):
    """NGO, Community kitchen, Small food business, Community group, Individual/household."""

    __tablename__ = "receiver_categories"
    __table_args__ = (
        CheckConstraint("priority_weight > 0 AND priority_weight <= 10", name="priority_weight_range"),
    )

    # Used later by matching and allocation ("configured priority rules", spec 6.4).
    # Seeded values are placeholders for admins to adjust, not policy decisions.
    priority_weight: Mapped[Decimal] = mapped_column(Numeric(4, 2), server_default=text("1.0"))
    requires_documents: Mapped[bool] = mapped_column(Boolean, server_default=true())


class FoodCategory(_CategoryColumns, Base):
    """Bakery items, Cooked meals, Fresh produce, Dairy, Packaged food, ..."""

    __tablename__ = "food_categories"

    perishability_class: Mapped[PerishabilityClass] = mapped_column(
        str_enum(PerishabilityClass, "perishability_class")
    )
    # Which date fields a listing in this category must include (used in the listings stage).
    requires_preparation_time: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    requires_expiry_date: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Admin-set conversion factors for the "estimated meals" report figure,
    # e.g. {"kg": 4, "portion": 1}. Always labelled as an estimate (plan issue H1).
    meals_per_unit: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class UrgencyRuleRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-food-category urgency thresholds (plan section 3.3 "urgency_rules").

    PLATFORM PRIORITISATION RULES set by administrators, not food-safety limits.
    The seeded values are demo placeholders (plan section 1.3).
    """

    __tablename__ = "urgency_rules"
    __table_args__ = (
        CheckConstraint("critical_hours > 0 AND critical_hours < high_hours AND high_hours < medium_hours",
                        name="threshold_order"),
        CheckConstraint("max_hours_since_preparation IS NULL OR max_hours_since_preparation > 0",
                        name="max_since_preparation_positive"),
    )

    food_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("food_categories.id", ondelete="CASCADE"), unique=True
    )
    medium_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    high_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    critical_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    max_hours_since_preparation: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # Plain id, no foreign key (like audit_logs.actor_id): it survives the admin's
    # account being deleted, and the audit log holds the full change history.
    updated_by: Mapped[uuid.UUID | None] = mapped_column()
