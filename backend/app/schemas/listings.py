"""Food listing request/response bodies.

Rules that need only the request itself are checked here. Rules that need
"now" or the database (category date rules, image ownership) are in
listing_service.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import COUNTABLE_UNITS, DistributionType, ListingStatus, QuantityUnit, UrgencyLevel
from app.schemas.lookups import FoodCategoryOut
from app.schemas.verification import FileOut


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


class ListingIn(BaseModel):
    """Used for both create (POST) and edit (PUT: send the full listing again).

    Times must include a time zone, e.g. "2026-11-15T18:00:00+05:00" (Pakistan time).
    A time without a zone is refused instead of guessed: a wrong guess would make
    every deadline up to 5 hours wrong (Technical Plan issue M11).
    """

    food_category_id: uuid.UUID
    food_name: str = Field(max_length=150, examples=["Chicken biryani"])
    description: str | None = Field(default=None, max_length=2000)
    unit: QuantityUnit
    total_quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2, examples=[30])

    preparation_at: AwareDatetime | None = None
    packaging_at: AwareDatetime | None = None
    best_before_at: AwareDatetime | None = None
    expiry_at: AwareDatetime | None = None

    pickup_window_start: AwareDatetime
    pickup_window_end: AwareDatetime
    # The last moment the food can be collected. Defaults to the end of the pickup window.
    collection_deadline: AwareDatetime | None = None

    # Leave all three empty to use the business address from your provider profile.
    pickup_address: str | None = Field(default=None, max_length=300)
    pickup_latitude: float | None = Field(default=None, ge=-90, le=90)
    pickup_longitude: float | None = Field(default=None, ge=-180, le=180)
    storage_info: str | None = Field(default=None, max_length=1000, examples=["Keep refrigerated"])

    distribution_type: DistributionType = DistributionType.FREE_DONATION
    image_file_id: uuid.UUID | None = None
    declaration_accepted: bool = Field(
        description="I confirm the information is accurate and the food has been handled "
                    "according to applicable standards."
    )

    @field_validator("food_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = _clean(value) or ""
        if len(cleaned) < 2:
            raise ValueError("Please enter the food name.")
        return cleaned

    @field_validator("description", "storage_info", "pickup_address")
    @classmethod
    def clean_optional(cls, value: str | None) -> str | None:
        return _clean(value)

    @model_validator(mode="after")
    def consistent(self) -> "ListingIn":
        if not self.declaration_accepted:
            raise ValueError("Please confirm the declaration that the information is accurate "
                             "and the food has been handled according to applicable standards.")
        if self.unit in COUNTABLE_UNITS and self.total_quantity % 1 != 0:
            raise ValueError(f"Quantity in '{self.unit.value}' must be a whole number.")
        if self.pickup_window_end <= self.pickup_window_start:
            raise ValueError("The pickup window must end after it starts.")
        if self.collection_deadline is None:
            self.collection_deadline = self.pickup_window_end
        if self.collection_deadline < self.pickup_window_start:
            raise ValueError("The collection deadline can't be before the pickup window starts.")
        location = (self.pickup_address, self.pickup_latitude, self.pickup_longitude)
        if any(v is not None for v in location) and not all(v is not None for v in location):
            raise ValueError("Give the pickup address, latitude and longitude together, "
                             "or leave all three empty to use your business address.")
        return self


class StatusChangeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ProviderCard(BaseModel):
    """Public business card shown on a listing. No phone number or contact person:
    contact details are shared only within an approved transaction (spec 15)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_name: str


class UrgencyRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    medium_hours: float
    high_hours: float
    critical_hours: float
    max_hours_since_preparation: float | None


class UrgencyOut(BaseModel):
    """Date-based urgency with its explanation. Calculated live on every request."""

    model_config = ConfigDict(from_attributes=True)

    level: UrgencyLevel
    explanation: str  # e.g. "High urgency: collection deadline in 3 h 20 m."
    effective_deadline: datetime  # the earliest relevant deadline
    deadline_source: str  # which date that was
    hours_left: float
    is_past_deadline: bool
    missing_fields: list[str]
    rule: UrgencyRuleOut  # the thresholds that were applied
    disclaimer: str  # always shown: this is not a food-safety assessment


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: ProviderCard
    food_category: FoodCategoryOut
    food_name: str
    description: str | None
    unit: QuantityUnit
    total_quantity: float
    allocated_quantity: float
    remaining_quantity: float
    preparation_at: datetime | None
    packaging_at: datetime | None
    best_before_at: datetime | None
    expiry_at: datetime | None
    pickup_window_start: datetime
    pickup_window_end: datetime
    collection_deadline: datetime
    pickup_address: str
    pickup_latitude: float
    pickup_longitude: float
    storage_info: str | None
    distribution_type: DistributionType
    image: FileOut | None
    status: ListingStatus
    urgency: UrgencyOut
    declaration_accepted: bool
    version: int  # send it back in the If-Match header when editing
    created_at: datetime
    updated_at: datetime


class StatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: str | None
    to_status: str
    changed_by: uuid.UUID | None
    reason: str | None
    created_at: datetime
