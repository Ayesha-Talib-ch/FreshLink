"""Volunteer assignment bodies (Technical Plan section 4.9)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AssignmentStatus, QuantityUnit


class StepIn(BaseModel):
    """A volunteer's progress update."""

    status: Literal["en_route_pickup", "collected", "en_route_dropoff", "delivered", "failed"]
    note: str | None = Field(default=None, max_length=500, description="Required (the reason) for 'failed'")
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2,
                                     description="Required for 'collected': the quantity you received")


class AssignIn(BaseModel):
    volunteer_id: uuid.UUID  # the volunteer PROFILE id (from suggested-volunteers)


class ContactProvider(BaseModel):
    business_name: str
    contact_person: str
    contact_phone: str


class ContactReceiver(BaseModel):
    receiver_name: str
    contact_name: str
    contact_phone: str


class VolunteerCard(BaseModel):
    full_name: str
    phone: str
    vehicle_type: str | None


class AssignmentOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    status: AssignmentStatus
    food_name: str
    unit: QuantityUnit
    quantity: float  # allocated quantity to deliver
    handed_over_quantity: float | None
    pickup_address: str
    pickup_latitude: float
    pickup_longitude: float
    scheduled_pickup_at: datetime | None
    collection_deadline: datetime
    delivery_distance_km: float  # pickup -> drop-off, straight line
    distance_km: float | None  # you -> pickup (open-task list only)
    # The next five are hidden from a volunteer until they accept the task (plan 5.3):
    dropoff_address: str | None
    dropoff_latitude: float | None
    dropoff_longitude: float | None
    provider_contact: ContactProvider | None
    receiver_contact: ContactReceiver | None
    volunteer: VolunteerCard | None
    decline_reason: str | None
    failure_reason: str | None
    accepted_at: datetime | None
    completed_at: datetime | None
    version: int
    created_at: datetime


class SuggestedVolunteer(BaseModel):
    id: uuid.UUID
    full_name: str
    has_vehicle: bool
    is_available_now: bool


class SuggestionOut(BaseModel):
    score: float
    components: dict[str, float]
    reasons: list[str]
    volunteer: SuggestedVolunteer


class ExcludedVolunteerOut(BaseModel):
    reason: str
    volunteer: SuggestedVolunteer


class SuggestionsOut(BaseModel):
    task_id: uuid.UUID
    suggestions: list[SuggestionOut]
    excluded: list[ExcludedVolunteerOut]


class AssignmentHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: str | None
    to_status: str
    changed_by: uuid.UUID | None
    note: str | None
    created_at: datetime
