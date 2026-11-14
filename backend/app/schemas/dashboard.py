"""Dashboard shapes (spec: provider / receiver / volunteer / administrator dashboards; plan 4.3 and 4.12).

Quantities are always grouped by unit: 10 kg and 10 packages can't be added (plan issue H1)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.domain.enums import QuantityUnit
from app.schemas.notifications import NotificationOut


class UnitQuantity(BaseModel):
    unit: QuantityUnit
    quantity: Decimal


class _Base(BaseModel):
    unread_notifications: int
    recent_notifications: list[NotificationOut]
    profile_complete: bool  # False until the role's profile exists (all counters are then 0)


class ProviderDashboard(_Base):
    role: Literal["provider"] = "provider"
    total_listings: int  # everything except removed listings
    active_listings: int
    urgent_listings: int  # active listings at high or critical urgency
    pending_requests: int
    approved_requests: int  # approved or partly approved, not yet completed
    completed_distributions: int  # collections the receiver confirmed
    expired_or_cancelled_listings: int  # expired, or closed by the provider


class ReceiverDashboard(_Base):
    role: Literal["receiver"] = "receiver"
    available_food: int  # listings they could request now (inside their pickup radius)
    active_requests: int  # pending + approved + partly approved
    pending_requests: int
    approved_requests: int
    rejected_requests: int
    completed_collections: int


class VolunteerDashboard(_Base):
    role: Literal["volunteer"] = "volunteer"
    available_assignments: int  # open tasks inside their service area
    assigned_tasks: int  # assigned by an admin, waiting for their answer
    accepted_tasks: int
    active_collections: int  # on the way, collected or delivered (not yet confirmed)
    completed_deliveries: int


class ActivityItem(BaseModel):
    at: datetime
    kind: str  # e.g. "user.registered", "listing.created", "request.status", "collection.received", "admin.action"
    summary: str
    entity_type: str | None
    entity_id: uuid.UUID | None


class AdminDashboard(_Base):
    role: Literal["admin"] = "admin"
    total_users: int
    total_providers: int
    total_receivers: int
    total_volunteers: int
    pending_verifications: int
    total_listings: int
    active_listings: int
    total_requests: int
    pending_requests: int
    approved_requests: int
    completed_distributions: int
    quantity_listed: list[UnitQuantity]
    quantity_allocated: list[UnitQuantity]
    quantity_collected: list[UnitQuantity]  # handed over by providers
    quantity_distributed: list[UnitQuantity]  # confirmed received by receivers
    expired_and_cancelled_listings: int
    recent_activity: list[ActivityItem]


Dashboard = ProviderDashboard | ReceiverDashboard | VolunteerDashboard | AdminDashboard
