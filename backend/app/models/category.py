"""Admin-managed categories (Technical Plan section 3.3, "Configuration").

The spec requires configurable categories (section 2.3: "configurable receiver
categories rather than permanently restricting receivers to only NGOs"), so
they are rows in tables, not hard-coded lists. The starting rows are inserted
by migration 0004; admin screens to edit them come in the admin stage.

`code` is a fixed identifier used by the program (e.g. "small_business");
`name` is the label users see and admins may rename.
"""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Integer, Numeric, String, Text, text, true
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
