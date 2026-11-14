"""Admin monitoring and moderation (spec 4.4.4 volunteers, 4.4.5 listings, 4.4.7 allocation monitoring;
plan 4.12).

Requests, allocations, collections and delivery tasks are monitored through the shared list endpoints,
which already return everything to administrators. This module adds what those can't show:
every listing in any status with date warnings, listing moderation, a volunteer overview and
the allocation-run history across all listings."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    AllocationRunPolicy,
    AllocationRunStatus,
    AllocationStatus,
    AssignmentStatus,
    ListingStatus,
    RequestStatus,
    UrgencyLevel,
    VerificationStatus,
)
from app.domain.state_machines import check_listing_admin_transition
from app.models.allocation import Allocation, AllocationRun
from app.models.assignment import VolunteerAssignment
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import ProviderProfile, VolunteerProfile
from app.models.request import FoodRequest
from app.models.user import User
from app.services import notification_service, request_service, urgency_service
from app.services.audit_service import ClientInfo, record

OPEN_LISTING = (ListingStatus.ACTIVE, ListingStatus.PAUSED, ListingStatus.FULLY_ALLOCATED)
# food still promised to someone and not yet handed over
UNDELIVERED_ALLOCATION = (AllocationStatus.CONFIRMED, AllocationStatus.IN_COLLECTION)
SOON_HOURS = 2


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _now() -> datetime:
    return datetime.now(UTC)


# ================================================================ listings monitor


def _date_warnings(listing: FoodListing) -> list[str]:
    """Plan 4.12 "date monitoring": problems an administrator should look at."""
    u = listing.urgency
    warnings: list[str] = []
    if u.missing_fields:
        warnings.append("missing required dates: " + ", ".join(u.missing_fields))
    if listing.status in OPEN_LISTING and u.is_past_deadline:
        warnings.append("past its deadline but still open (the expiry scan hasn't run yet)")
    elif listing.status in OPEN_LISTING and u.hours_left <= SOON_HOURS:
        warnings.append(f"deadline within {SOON_HOURS} hours")
    return warnings


def list_listings(db: Session, *, q: str | None, status: ListingStatus | None, urgency: UrgencyLevel | None,
                  food_category_id: uuid.UUID | None, provider_id: uuid.UUID | None,
                  deadline_within_hours: float | None, needs_attention: bool,
                  sort: Literal["newest", "deadline"], page: int, page_size: int) -> dict[str, Any]:
    query = select(FoodListing)
    search = (q or "").strip()
    if search:
        like = f"%{_escape_like(search)}%"
        query = query.where(or_(FoodListing.food_name.ilike(like, escape="\\"),
                                FoodListing.provider_id.in_(select(ProviderProfile.id).where(
                                    ProviderProfile.business_name.ilike(like, escape="\\")))))
    if status is not None:
        query = query.where(FoodListing.status == status)
    if urgency is not None:
        query = query.where(FoodListing.urgency_level == urgency)
    if food_category_id is not None:
        query = query.where(FoodListing.food_category_id == food_category_id)
    if provider_id is not None:
        query = query.where(FoodListing.provider_id == provider_id)
    if deadline_within_hours is not None:
        query = query.where(FoodListing.status.in_(OPEN_LISTING),
                            FoodListing.collection_deadline <= _now() + timedelta(hours=deadline_within_hours))
    if needs_attention:  # open listings that are critical, missing dates, or already past the deadline
        query = query.where(FoodListing.status.in_(OPEN_LISTING), or_(
            FoodListing.urgency_level.in_((UrgencyLevel.CRITICAL, UrgencyLevel.INCOMPLETE)),
            FoodListing.collection_deadline <= _now() + timedelta(hours=SOON_HOURS)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = ([FoodListing.collection_deadline, FoodListing.id] if sort == "deadline"
             else [FoodListing.created_at.desc(), FoodListing.id])
    items = list(db.scalars(query.order_by(*order).offset((page - 1) * page_size).limit(page_size)))
    urgency_service.attach(db, items)

    ids = [listing.id for listing in items]
    pending = dict(db.execute(select(FoodRequest.listing_id, func.count()).where(
        FoodRequest.listing_id.in_(ids), FoodRequest.status == RequestStatus.PENDING)
        .group_by(FoodRequest.listing_id)).all())
    promised = dict(db.execute(select(Allocation.listing_id, func.count()).where(
        Allocation.listing_id.in_(ids), Allocation.status.in_(UNDELIVERED_ALLOCATION))
        .group_by(Allocation.listing_id)).all())
    for listing in items:
        listing.pending_requests = pending.get(listing.id, 0)
        listing.undelivered_allocations = promised.get(listing.id, 0)
        listing.date_warnings = _date_warnings(listing)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ============================================================== listing moderation

_TARGET = {"suspend": ListingStatus.SUSPENDED, "reinstate": ListingStatus.ACTIVE,
           "close": ListingStatus.CLOSED, "remove": ListingStatus.REMOVED}
_TITLES = {"suspend": "Listing suspended", "reinstate": "Listing reinstated",
           "close": "Listing closed by an administrator", "remove": "Listing removed"}


def moderate(db: Session, admin: User, listing_id: uuid.UUID, action: str, reason: str,
             client: ClientInfo) -> FoodListing:
    """Spec 4.4.5. Every action needs a reason, is audited and tells the provider why."""
    target = _TARGET[action]
    listing = db.scalar(select(FoodListing).where(FoodListing.id == listing_id).with_for_update(of=FoodListing))
    if listing is None:
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    check_listing_admin_transition(listing.status, target)
    urgency_service.attach(db, [listing])
    if action == "reinstate" and listing.urgency.is_past_deadline:
        raise AppError(409, "DEADLINE_PASSED", "The collection deadline has passed; close the listing instead.")
    if action == "remove":
        promised = db.scalar(select(func.count()).where(
            Allocation.listing_id == listing.id, Allocation.status.in_(UNDELIVERED_ALLOCATION)))
        if promised:
            # Food already promised to receivers: cancel those allocations first (with a reason each),
            # so nobody travels to collect food from a removed listing.
            raise AppError(409, "LISTING_HAS_ALLOCATIONS",
                           "Food from this listing is still promised to receivers. Cancel those allocations first.",
                           {"undelivered_allocations": promised})
    if action == "reinstate" and listing.remaining_quantity <= 0:
        target = ListingStatus.FULLY_ALLOCATED

    previous = listing.status
    listing.status = target
    listing.version += 1
    db.add(ListingStatusHistory(listing_id=listing.id, from_status=previous.value, to_status=target.value,
                                changed_by=admin.id, reason=f"administrator: {reason}"))
    if target in (ListingStatus.CLOSED, ListingStatus.REMOVED):
        request_service.expire_pending_for_listing(db, listing, admin.id, f"listing {target.value} by an administrator")
    notification_service.notify(db, [listing.provider.user_id], "admin_alert", _TITLES[action],
                                f"{listing.food_name}: {reason}", "listing", listing.id)
    record(db, actor=admin, action=f"listing.{action}", entity_type="listing", entity_id=listing.id,
           client=client, reason=reason, before={"status": previous.value}, after={"status": target.value})
    db.commit()
    db.refresh(listing)
    urgency_service.attach(db, [listing])
    listing.pending_requests = db.scalar(select(func.count()).where(
        FoodRequest.listing_id == listing.id, FoodRequest.status == RequestStatus.PENDING))
    listing.undelivered_allocations = db.scalar(select(func.count()).where(
        Allocation.listing_id == listing.id, Allocation.status.in_(UNDELIVERED_ALLOCATION)))
    listing.date_warnings = _date_warnings(listing)
    return listing


# ================================================================== volunteers

ACTIVE_TASK = (AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE_PICKUP,
               AssignmentStatus.COLLECTED, AssignmentStatus.EN_ROUTE_DROPOFF, AssignmentStatus.DELIVERED)


def list_volunteers(db: Session, *, q: str | None, available_now: bool | None,
                    verification_status: VerificationStatus | None, page: int, page_size: int) -> dict[str, Any]:
    """Spec 4.4.4: availability, current load and how reliable each volunteer has been."""
    query = select(VolunteerProfile).join(User, User.id == VolunteerProfile.user_id).where(User.deleted_at.is_(None))
    search = (q or "").strip()
    if search:
        like = f"%{_escape_like(search)}%"
        query = query.where(or_(User.full_name.ilike(like, escape="\\"), User.email.ilike(like, escape="\\"),
                                VolunteerProfile.address.ilike(like, escape="\\")))
    if available_now is not None:
        query = query.where(VolunteerProfile.is_available_now.is_(available_now))
    if verification_status is not None:
        query = query.where(User.verification_status == verification_status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    profiles = list(db.scalars(query.order_by(User.full_name, VolunteerProfile.id)
                               .offset((page - 1) * page_size).limit(page_size)))

    status = VolunteerAssignment.status
    counts = {row.volunteer_id: row for row in db.execute(select(
        VolunteerAssignment.volunteer_id,
        func.count().filter(status.in_(ACTIVE_TASK)).label("active"),
        func.count().filter(status == AssignmentStatus.COMPLETED).label("completed"),
        func.count().filter(status == AssignmentStatus.FAILED).label("failed"),
        func.count().filter(status == AssignmentStatus.DECLINED).label("declined"),
    ).where(VolunteerAssignment.volunteer_id.in_([p.id for p in profiles]))
        .group_by(VolunteerAssignment.volunteer_id)).all()}
    items = []
    for p in profiles:
        c = counts.get(p.id)
        active, completed, failed, declined = (c.active, c.completed, c.failed, c.declined) if c else (0, 0, 0, 0)
        finished = completed + failed
        items.append({
            "id": p.id, "user_id": p.user_id, "full_name": p.user.full_name, "email": p.user.email,
            "phone": p.user.phone, "account_status": p.user.account_status,
            "verification_status": p.user.verification_status, "address": p.address,
            "has_vehicle": p.has_vehicle, "vehicle_type": p.vehicle_type,
            "service_radius_km": float(p.service_radius_km), "is_available_now": p.is_available_now,
            "availability": p.availability, "active_tasks": active, "completed_deliveries": completed,
            "failed_deliveries": failed, "declined_tasks": declined,
            # None until they have finished at least one delivery (no misleading 0 % for newcomers)
            "completion_rate": round(completed / finished, 2) if finished else None,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ============================================================ allocation runs


def list_allocation_runs(db: Session, *, status: AllocationRunStatus | None, policy: AllocationRunPolicy | None,
                         listing_id: uuid.UUID | None, overridden_only: bool, page: int,
                         page_size: int) -> dict[str, Any]:
    """Spec 4.4.7: proposed allocations vs what was approved, and where people overrode the suggestion."""
    overrides = (select(Allocation.run_id, func.count().label("n")).where(Allocation.override_reason.is_not(None))
                 .group_by(Allocation.run_id).subquery())
    query = select(AllocationRun, func.coalesce(overrides.c.n, 0)).outerjoin(overrides, overrides.c.run_id == AllocationRun.id)
    if status is not None:
        query = query.where(AllocationRun.status == status)
    if policy is not None:
        query = query.where(AllocationRun.policy == policy)
    if listing_id is not None:
        query = query.where(AllocationRun.listing_id == listing_id)
    if overridden_only:
        query = query.where(overrides.c.n > 0)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(AllocationRun.created_at.desc(), AllocationRun.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()

    run_ids = [run.id for run, _ in rows]
    approved = dict(db.execute(select(Allocation.run_id, func.sum(Allocation.approved_quantity)).where(
        Allocation.run_id.in_(run_ids), Allocation.status != AllocationStatus.CANCELLED)
        .group_by(Allocation.run_id)).all())
    listings = {li.id: li for li in db.scalars(select(FoodListing).where(
        FoodListing.id.in_({run.listing_id for run, _ in rows})))}
    items = []
    for run, override_count in rows:
        listing = listings[run.listing_id]
        items.append({
            "id": run.id, "listing_id": run.listing_id, "food_name": listing.food_name, "unit": listing.unit,
            "provider_name": listing.provider.business_name, "policy": run.policy, "status": run.status,
            "available_snapshot": float(run.available_snapshot),
            "suggested_total": float(sum(line.suggested_quantity for line in run.lines)),
            "approved_total": float(approved.get(run.id, 0)) if run.status == AllocationRunStatus.CONFIRMED else None,
            "requests": len(run.lines), "overrides": override_count,
            "created_at": run.created_at, "confirmed_at": run.confirmed_at,
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}

