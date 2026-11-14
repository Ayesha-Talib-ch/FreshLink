"""Admin monitoring and moderation, and problem reports (plan 4.8 / 4.12)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import (
    AccountStatus,
    AllocationRunPolicy,
    AllocationRunStatus,
    IssueCategory,
    IssueEntityType,
    IssueStatus,
    QuantityUnit,
    UserRole,
    VehicleType,
    VerificationStatus,
)
from app.schemas.listings import ListingOut
from app.schemas.profiles import AvailabilitySlot


class AdminListingOut(ListingOut):
    pending_requests: int
    undelivered_allocations: int  # food promised to receivers, not yet handed over
    date_warnings: list[str]  # e.g. "missing required dates: expiry_at", "deadline within 2 hours"


class ModerationIn(BaseModel):
    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def need_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Please give a reason (at least 5 characters). The provider sees it.")
        return value


class AdminVolunteerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID  # volunteer profile id (used by /assignments/{id}/assign)
    user_id: uuid.UUID
    full_name: str
    email: str
    phone: str
    account_status: AccountStatus
    verification_status: VerificationStatus
    address: str
    has_vehicle: bool
    vehicle_type: VehicleType | None
    service_radius_km: float
    is_available_now: bool
    availability: list[AvailabilitySlot]
    active_tasks: int
    completed_deliveries: int
    failed_deliveries: int
    declined_tasks: int
    completion_rate: float | None  # completed / (completed + failed); None before the first finished delivery


class AdminRunOut(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    food_name: str
    unit: QuantityUnit
    provider_name: str
    policy: AllocationRunPolicy
    status: AllocationRunStatus
    available_snapshot: float
    suggested_total: float
    approved_total: float | None  # only for confirmed runs
    requests: int
    overrides: int  # lines where a person changed the suggested quantity (with a reason)
    created_at: datetime
    confirmed_at: datetime | None


# ------------------------------------------------------------------ problem reports


class IssueIn(BaseModel):
    entity_type: IssueEntityType
    entity_id: uuid.UUID
    category: IssueCategory
    description: str = Field(max_length=2000)

    @field_validator("description")
    @classmethod
    def describe(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("Please describe the problem (at least 10 characters).")
        return value


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reporter_id: uuid.UUID
    reporter_name: str | None
    reporter_role: UserRole | None
    entity_type: IssueEntityType
    entity_id: uuid.UUID
    category: IssueCategory
    description: str
    status: IssueStatus
    resolution_note: str | None
    resolved_by_email: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IssueUpdateIn(BaseModel):
    status: IssueStatus
    resolution_note: str | None = Field(default=None, max_length=2000)

    @field_validator("resolution_note")
    @classmethod
    def strip(cls, value: str | None) -> str | None:
        return (value or "").strip() or None
