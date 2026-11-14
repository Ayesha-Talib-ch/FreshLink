"""Profile request/response bodies for providers, receivers and volunteers.

Input rules are checked here (shape, ranges, formats). Rules that need the
database (e.g. "is this category active?") are checked in profile_service.
"""

import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import COUNTABLE_UNITS, QuantityUnit, VehicleType
from app.domain.validation import normalize_phone
from app.schemas.lookups import CategoryOut, FoodCategoryOut, ReceiverCategoryOut

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _clean_text(value: str, label: str, minimum: int = 2) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) < minimum:
        raise ValueError(f"Please enter the {label}.")
    return cleaned


# ------------------------------------------------------------------ shared parts


class LocationIn(BaseModel):
    """Address text + map pin. Distances are calculated from latitude/longitude
    on the server (Haversine formula), so no paid map API is needed."""

    address: str = Field(max_length=300, examples=["Shop 12, Main Boulevard, Gulberg III, Lahore"])
    latitude: float = Field(ge=-90, le=90, examples=[31.5204])
    longitude: float = Field(ge=-180, le=180, examples=[74.3587])

    @field_validator("address")
    @classmethod
    def clean_address(cls, value: str) -> str:
        return _clean_text(value, "address", minimum=5)


class TimeRange(BaseModel):
    open: time
    close: time

    @model_validator(mode="after")
    def close_after_open(self) -> "TimeRange":
        if self.close <= self.open:
            raise ValueError("Closing time must be after opening time.")
        return self


# --------------------------------------------------------------------- provider


class ProviderProfileIn(LocationIn):
    business_name: str = Field(max_length=150, examples=["Lahore Fresh Bakers"])
    provider_category_id: uuid.UUID
    contact_person: str = Field(max_length=120)
    contact_phone: str = Field(max_length=30, examples=["0300 1234567"])
    # e.g. {"mon": [{"open": "09:00", "close": "22:00"}]} - days left out = closed
    opening_hours: dict[Day, list[TimeRange]] | None = None

    @field_validator("business_name")
    @classmethod
    def clean_business(cls, value: str) -> str:
        return _clean_text(value, "business name")

    @field_validator("contact_person")
    @classmethod
    def clean_person(cls, value: str) -> str:
        return _clean_text(value, "contact person's name")

    @field_validator("contact_phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("opening_hours")
    @classmethod
    def no_overlapping_hours(cls, value: dict[str, list[TimeRange]] | None) -> dict | None:
        if value is None:
            return None
        for day, ranges in value.items():
            if len(ranges) > 3:
                raise ValueError(f"At most 3 opening periods per day ({day}).")
            ordered = sorted(ranges, key=lambda r: r.open)
            for earlier, later in zip(ordered, ordered[1:]):
                if later.open < earlier.close:
                    raise ValueError(f"Opening periods overlap on {day}.")
        return value


class ProviderProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_name: str
    category: CategoryOut
    address: str
    latitude: float
    longitude: float
    contact_person: str
    contact_phone: str
    opening_hours: dict[str, list[TimeRange]] | None
    updated_at: datetime


# --------------------------------------------------------------------- receiver


class ReceiverProfileIn(LocationIn):
    receiver_name: str = Field(max_length=150, examples=["Hope Welfare Trust"])
    receiver_category_id: uuid.UUID
    max_pickup_radius_km: Decimal = Field(default=Decimal("10"), gt=0, le=100, decimal_places=1)
    has_own_transport: bool = False
    # Required for the "Individual / household" category; ignored for others.
    household_size: int | None = Field(default=None, ge=1, le=50)
    # Required for the "Small food business" category.
    not_for_resale_declared: bool = False

    @field_validator("receiver_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean_text(value, "receiver or organization name")


class FoodPreferenceIn(BaseModel):
    food_category_id: uuid.UUID
    max_quantity_per_pickup: Decimal = Field(gt=0, max_digits=10, decimal_places=2, examples=[20])
    unit: QuantityUnit

    @model_validator(mode="after")
    def whole_number_for_countable_units(self) -> "FoodPreferenceIn":
        if self.unit in COUNTABLE_UNITS and self.max_quantity_per_pickup % 1 != 0:
            raise ValueError(f"Quantity in '{self.unit.value}' must be a whole number.")
        return self


class FoodPreferencesIn(BaseModel):
    """Replaces the whole list (send [] to clear it)."""

    preferences: list[FoodPreferenceIn] = Field(max_length=20)

    @field_validator("preferences")
    @classmethod
    def one_entry_per_category(cls, value: list[FoodPreferenceIn]) -> list[FoodPreferenceIn]:
        ids = [p.food_category_id for p in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Each food category can only appear once.")
        return value


class FoodPreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    food_category: FoodCategoryOut
    max_quantity_per_pickup: float
    unit: QuantityUnit


class ReceiverProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    receiver_name: str
    category: ReceiverCategoryOut
    address: str
    latitude: float
    longitude: float
    max_pickup_radius_km: float
    has_own_transport: bool
    household_size: int | None
    not_for_resale_declared: bool
    is_eligible: bool  # set by administrators
    food_preferences: list[FoodPreferenceOut]
    updated_at: datetime


# -------------------------------------------------------------------- volunteer


class VolunteerProfileIn(LocationIn):
    has_vehicle: bool = False
    vehicle_type: VehicleType | None = None
    service_radius_km: Decimal = Field(default=Decimal("5"), gt=0, le=100, decimal_places=1)
    is_available_now: bool = False

    @model_validator(mode="after")
    def vehicle_type_only_with_vehicle(self) -> "VolunteerProfileIn":
        if not self.has_vehicle and self.vehicle_type is not None:
            raise ValueError("Choose a vehicle type only if you have a vehicle.")
        return self


class AvailabilitySlot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day_of_week: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def end_after_start(self) -> "AvailabilitySlot":
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after start time.")
        return self


class AvailabilityIn(BaseModel):
    """Replaces the whole weekly schedule (send [] to clear it)."""

    slots: list[AvailabilitySlot] = Field(max_length=28)

    @field_validator("slots")
    @classmethod
    def no_overlaps(cls, value: list[AvailabilitySlot]) -> list[AvailabilitySlot]:
        ordered = sorted(value, key=lambda s: (s.day_of_week, s.start_time))
        for earlier, later in zip(ordered, ordered[1:]):
            if later.day_of_week == earlier.day_of_week and later.start_time < earlier.end_time:
                raise ValueError(f"Time slots overlap on {DAYS[later.day_of_week]}.")
        return ordered


class VolunteerProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    address: str
    latitude: float
    longitude: float
    has_vehicle: bool
    vehicle_type: VehicleType | None
    service_radius_km: float
    is_available_now: bool
    availability: list[AvailabilitySlot]
    updated_at: datetime


# ------------------------------------------------------------------ user account


class UserUpdateIn(BaseModel):
    """Common account fields any role can change. Email changes come later
    (they need an email confirmation step)."""

    full_name: str = Field(max_length=120)
    phone: str = Field(max_length=30)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean_text(value, "full name")

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_phone(value)
