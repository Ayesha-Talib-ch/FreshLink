"""Collection request/response bodies (Technical Plan section 4.8)."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import CollectionMethod, CollectionStatus
from app.schemas.requests import RequestListingSummary


def _clean(value: str | None) -> str | None:
    return (" ".join(value.split()) or None) if value else None


class ScheduleIn(BaseModel):
    method: CollectionMethod | None = None
    scheduled_pickup_at: AwareDatetime | None = None


class HandoverIn(BaseModel):
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2, description="Quantity actually handed over")
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: str | None) -> str | None:
        return _clean(value)


class ReceiptIn(BaseModel):
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2, description="Quantity actually received")
    notes: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500, description="Required when an administrator confirms")

    @field_validator("notes")
    @classmethod
    def clean_notes(cls, value: str | None) -> str | None:
        return _clean(value)


class ProviderContact(BaseModel):
    """Shared with the receiver (and volunteer) inside this authorized transaction."""

    model_config = ConfigDict(from_attributes=True)

    business_name: str
    contact_person: str
    contact_phone: str
    address: str
    latitude: float
    longitude: float


class ReceiverContact(BaseModel):
    """Shared with the provider (and volunteer) inside this authorized transaction."""

    model_config = ConfigDict(from_attributes=True)

    receiver_name: str
    contact_name: str
    contact_phone: str
    address: str
    latitude: float
    longitude: float
    has_own_transport: bool


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    allocation_id: uuid.UUID
    request_id: uuid.UUID
    status: CollectionStatus
    method: CollectionMethod
    scheduled_pickup_at: datetime | None
    listing: RequestListingSummary
    provider: ProviderContact
    receiver: ReceiverContact
    approved_quantity: float  # what was allocated
    handed_over_quantity: float | None  # what left the provider
    handed_over_at: datetime | None
    received_quantity: float | None  # what actually arrived ("distributed")
    received_at: datetime | None
    failure_reason: str | None
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime
