"""Role profiles: the role-specific details of each account (Technical Plan section 3.3).

Each user has at most ONE profile, matching their role (user_id is unique).
Approval status is NOT stored here; it lives only on users.verification_status
(single source of truth, plan section 3.4).
"""

import uuid
from datetime import time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Double,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    false,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import QuantityUnit, VehicleType
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum

def _location_checks() -> tuple[CheckConstraint, CheckConstraint]:
    # A NEW pair for each table: one constraint object can't belong to two tables.
    return (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)


class ProviderProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_profiles"
    __table_args__ = _location_checks()

    user_id: Mapped[uuid.UUID] = _user_fk()
    business_name: Mapped[str] = mapped_column(String(150))
    # RESTRICT: a category that providers use can't be deleted (only deactivated).
    provider_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_categories.id", ondelete="RESTRICT"), index=True
    )
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float] = mapped_column(Double)
    longitude: Mapped[float] = mapped_column(Double)
    contact_person: Mapped[str] = mapped_column(String(120))
    contact_phone: Mapped[str] = mapped_column(String(16))
    # e.g. {"mon": [{"open": "09:00", "close": "22:00"}], "fri": [...]}
    opening_hours: Mapped[dict | None] = mapped_column(JSONB)

    category: Mapped[ProviderCategory] = relationship(lazy="joined")


class ReceiverProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "receiver_profiles"
    __table_args__ = (
        *_location_checks(),
        CheckConstraint("max_pickup_radius_km > 0 AND max_pickup_radius_km <= 100", name="radius_range"),
        CheckConstraint("household_size IS NULL OR household_size BETWEEN 1 AND 50", name="household_size_range"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    receiver_name: Mapped[str] = mapped_column(String(150))
    receiver_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receiver_categories.id", ondelete="RESTRICT"), index=True
    )
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float] = mapped_column(Double)
    longitude: Mapped[float] = mapped_column(Double)
    max_pickup_radius_km: Mapped[Decimal] = mapped_column(Numeric(5, 1), server_default=text("10"))
    has_own_transport: Mapped[bool] = mapped_column(Boolean, server_default=false())
    household_size: Mapped[int | None] = mapped_column(SmallInteger)  # individuals/households only
    # Managed by admins only ("Manage receiver eligibility", spec 4.4.3).
    is_eligible: Mapped[bool] = mapped_column(Boolean, server_default=true())
    eligibility_notes: Mapped[str | None] = mapped_column(Text)
    # Required for small food businesses: donated food must not be resold (plan issue M6).
    not_for_resale_declared: Mapped[bool] = mapped_column(Boolean, server_default=false())

    category: Mapped[ReceiverCategory] = relationship(lazy="joined")
    # The account (name, phone). Loaded on demand; used when a provider views a request.
    user: Mapped["User"] = relationship(lazy="select")  # noqa: F821
    food_preferences: Mapped[list["ReceiverFoodPreference"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ReceiverFoodPreference.food_category_id"
    )

    # Shown to a provider only inside a request for THEIR listing (plan section 5.3).
    @property
    def category_name(self) -> str:
        return self.category.name

    @property
    def contact_name(self) -> str:
        return self.user.full_name

    @property
    def contact_phone(self) -> str:
        return self.user.phone


class ReceiverFoodPreference(TimestampMixin, Base):
    """What a receiver accepts and how much per pickup. This IS the receiver's
    "capacity", stored per food category and unit (fixes plan issue H2)."""

    __tablename__ = "receiver_food_preferences"
    __table_args__ = (
        CheckConstraint("max_quantity_per_pickup > 0", name="max_quantity_positive"),
    )

    receiver_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receiver_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    food_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("food_categories.id", ondelete="RESTRICT"), primary_key=True
    )
    max_quantity_per_pickup: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit: Mapped[QuantityUnit] = mapped_column(str_enum(QuantityUnit, "quantity_unit"))

    food_category: Mapped[FoodCategory] = relationship(lazy="joined")


class VolunteerProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "volunteer_profiles"
    __table_args__ = (
        *_location_checks(),
        CheckConstraint("service_radius_km > 0 AND service_radius_km <= 100", name="radius_range"),
        CheckConstraint("has_vehicle OR vehicle_type IS NULL", name="vehicle_type_needs_vehicle"),
    )

    user_id: Mapped[uuid.UUID] = _user_fk()
    address: Mapped[str] = mapped_column(String(300))
    latitude: Mapped[float] = mapped_column(Double)
    longitude: Mapped[float] = mapped_column(Double)
    has_vehicle: Mapped[bool] = mapped_column(Boolean, server_default=false())
    vehicle_type: Mapped[VehicleType | None] = mapped_column(str_enum(VehicleType, "vehicle_type"))
    service_radius_km: Mapped[Decimal] = mapped_column(Numeric(5, 1), server_default=text("5"))
    is_available_now: Mapped[bool] = mapped_column(Boolean, server_default=false())

    user: Mapped["User"] = relationship(lazy="select")  # noqa: F821  (name, phone)
    availability: Mapped[list["VolunteerAvailability"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin",
        order_by="(VolunteerAvailability.day_of_week, VolunteerAvailability.start_time)",
    )


class VolunteerAvailability(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Weekly time slots, e.g. Monday 17:00-21:00 (times in Pakistan local time)."""

    __tablename__ = "volunteer_availability"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_of_week_range"),  # 0 = Monday
        CheckConstraint("end_time > start_time", name="end_after_start"),
    )

    volunteer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("volunteer_profiles.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

