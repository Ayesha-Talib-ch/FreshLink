"""Food request bodies (spec 4.2.3 "Request Food", Technical Plan section 4.5)."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import AllocationStatus, CollectionPreference, ListingStatus, QuantityUnit, RequestStatus
from app.schemas.listings import ProviderCard


class _RequestFields(BaseModel):
    requested_quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2, examples=[10])
    purpose: str | None = Field(default=None, max_length=500, examples=["Evening meal for 40 children"])
    collection_preference: CollectionPreference = CollectionPreference.SELF_PICKUP
    # Must fall inside the listing's pickup window. Include the time zone (+05:00).
    preferred_pickup_at: AwareDatetime | None = None

    @field_validator("purpose")
    @classmethod
    def clean_purpose(cls, value: str | None) -> str | None:
        return " ".join(value.split()) or None if value else None


class RequestIn(_RequestFields):
    listing_id: uuid.UUID


class RequestUpdateIn(_RequestFields):
    """Edit a PENDING request (send all fields again)."""


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RequestListingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    food_name: str
    unit: QuantityUnit
    remaining_quantity: float
    status: ListingStatus
    pickup_window_start: datetime
    pickup_window_end: datetime
    collection_deadline: datetime
    pickup_address: str
    provider: ProviderCard


class RequestReceiverSummary(BaseModel):
    """Shown to the provider of the requested listing (plan 5.3: contact details only
    within an authorized transaction, i.e. once the receiver has requested their food)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    receiver_name: str
    category_name: str
    address: str
    latitude: float
    longitude: float
    has_own_transport: bool
    contact_name: str
    contact_phone: str


class AllocationBrief(BaseModel):
    """Shown on a request: the decision about it, if any (allocation stage)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    approved_quantity: float
    status: AllocationStatus
    decision_reason: str


class RequestMatch(BaseModel):
    """How well this receiver matches the listing (matching v1); provider view only."""

    score: float | None  # None when the receiver is excluded by a hard filter
    rank: int | None
    reasons: list[str]
    excluded_reason: str | None


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    listing: RequestListingSummary
    receiver: RequestReceiverSummary
    requested_quantity: float
    purpose: str | None
    collection_preference: CollectionPreference
    preferred_pickup_at: datetime | None
    status: RequestStatus
    allocation: AllocationBrief | None = None  # the approved quantity, once decided
    rejection_reason: str | None
    responded_at: datetime | None
    distance_km: float  # pickup point -> receiver's saved location (straight line)
    match: RequestMatch | None = None  # only on GET /listings/{id}/requests
    version: int
    created_at: datetime
    updated_at: datetime


class RequestHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: str | None
    to_status: str
    changed_by: uuid.UUID | None
    reason: str | None
    created_at: datetime
