"""Admin reports, fairness and the audit-log viewer (plan 4.12)."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.domain.enums import QuantityUnit
from app.schemas.dashboard import UnitQuantity

Quantities = list[UnitQuantity]


class SummaryReport(BaseModel):
    filters: dict[str, Any]
    listings: int
    requests: int
    requests_by_status: dict[str, int]
    allocations: int
    successful_redistributions: int  # collections the receiver confirmed
    failed_collections: int
    quantity_listed: Quantities
    quantity_allocated: Quantities
    quantity_collected: Quantities  # handed over by providers
    quantity_distributed: Quantities  # confirmed received
    quantity_not_redistributed: Quantities  # left over on expired or closed listings


class PeriodRow(BaseModel):
    period_start: date
    listings: int
    requests: int
    completed_distributions: int
    quantity_listed: Quantities
    quantity_distributed: Quantities


class TimeseriesReport(BaseModel):
    filters: dict[str, Any]
    interval: Literal["week", "month"]
    rows: list[PeriodRow]


class CategoryRow(BaseModel):
    food_category_id: uuid.UUID
    name: str
    listings: int
    requests: int
    quantity_listed: Quantities
    quantity_distributed: Quantities


class ProviderRow(BaseModel):
    provider_id: uuid.UUID
    name: str
    listings: int
    completed_distributions: int
    quantity_listed: Quantities
    quantity_distributed: Quantities


class ReceiverRow(BaseModel):
    receiver_id: uuid.UUID
    name: str
    category: str
    requests: int
    approved_requests: int
    rejected_requests: int
    completed_collections: int
    quantity_received: Quantities


class VolunteerRow(BaseModel):
    volunteer_id: uuid.UUID
    name: str
    tasks: int
    completed_deliveries: int
    failed_deliveries: int
    declined_tasks: int
    completion_rate: float | None


class CategoryReport(BaseModel):
    filters: dict[str, Any]
    rows: list[CategoryRow]


class ProviderReport(BaseModel):
    filters: dict[str, Any]
    rows: list[ProviderRow]


class ReceiverReport(BaseModel):
    filters: dict[str, Any]
    rows: list[ReceiverRow]


class VolunteerReport(BaseModel):
    filters: dict[str, Any]
    rows: list[VolunteerRow]


class ShareQuantity(UnitQuantity):
    share: float  # of everything received in this unit, 0..1


class CategoryFairness(BaseModel):
    receiver_category_id: uuid.UUID
    name: str
    receivers: int
    decided_requests: int
    rejected_requests: int
    average_fill_ratio: float | None  # 0 = all rejected, 1 = everything approved in full
    quantity_received: list[ShareQuantity]


class UnitInequality(BaseModel):
    unit: QuantityUnit
    receivers: int
    gini: float | None  # 0 = perfectly equal


class FairnessReport(BaseModel):
    filters: dict[str, Any]
    by_receiver_category: list[CategoryFairness]
    inequality_by_unit: list[UnitInequality]
    allocations: int
    overridden_allocations: int
    override_rate: float | None
    note: str


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    actor_id: uuid.UUID | None
    actor_email: str | None
    actor_role: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    reason: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    user_agent: str | None
