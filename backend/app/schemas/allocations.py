"""Allocation request/response bodies (Technical Plan section 4.7)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import AllocationRunPolicy, AllocationRunStatus, AllocationStatus
from app.schemas.requests import RequestListingSummary


class AllocationRunIn(BaseModel):
    policy: Literal["priority_weighted", "proportional_fair", "first_come"] = "priority_weighted"


class LineChange(BaseModel):
    request_id: uuid.UUID
    approved_quantity: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    # Required whenever approved_quantity differs from the suggestion (spec 4.4.7).
    override_reason: str | None = Field(default=None, max_length=500)


class ConfirmIn(BaseModel):
    """Leave `lines` empty to accept the suggestion exactly as it is."""

    lines: list[LineChange] = Field(default_factory=list, max_length=200)

    @field_validator("lines")
    @classmethod
    def unique_requests(cls, value: list[LineChange]) -> list[LineChange]:
        if len({line.request_id for line in value}) != len(value):
            raise ValueError("Each request can appear only once.")
        return value


class ApproveIn(BaseModel):
    """Approve one request. Leave approved_quantity empty to approve the suggested amount
    (the full request if possible)."""

    approved_quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    override_reason: str | None = Field(default=None, max_length=500)


class RunLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: uuid.UUID
    receiver_name: str
    receiver_category: str
    requested_at: datetime
    requested_quantity: float
    cap_quantity: float  # min(requested, receiver capacity); 0 when not eligible
    priority_weight: float
    suggested_quantity: float
    explanation: str


class AllocationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    listing_id: uuid.UUID
    policy: AllocationRunPolicy
    solver: str
    status: AllocationRunStatus
    available_snapshot: float
    lines: list[RunLineOut]
    created_at: datetime
    confirmed_at: datetime | None


class AllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    request_id: uuid.UUID
    listing: RequestListingSummary
    receiver_id: uuid.UUID
    receiver_name: str
    suggested_quantity: float
    approved_quantity: float
    status: AllocationStatus
    decision_reason: str
    override_reason: str | None
    cancelled_reason: str | None
    created_at: datetime

