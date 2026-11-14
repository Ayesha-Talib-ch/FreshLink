"""Dashboards for the four roles and the admin activity feed (spec "Dashboard" sections; plan 4.3 / 4.12).

Every number is a COUNT or SUM straight from the database. Nothing is cached or estimated, so the
dashboard can never disagree with the lists behind it. Quantities are summed per unit (plan 3.5)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    ACTIVE_REQUEST_STATUSES,
    AllocationStatus,
    AssignmentStatus,
    CollectionStatus,
    ListingStatus,
    RequestStatus,
    UrgencyLevel,
    UserRole,
    VerificationRequestStatus,
)
from app.domain.formatting import format_quantity
from app.models.allocation import Allocation
from app.models.assignment import AssignmentStatusHistory, VolunteerAssignment
from app.models.audit import AuditLog
from app.models.collection import Collection
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import ProviderProfile, ReceiverProfile, VolunteerProfile
from app.models.request import FoodRequest, RequestStatusHistory
from app.models.user import User
from app.models.verification import VerificationRequest
from app.services import browse_service, notification_service

APPROVED_STATUSES = (RequestStatus.APPROVED, RequestStatus.PARTIALLY_APPROVED)
ENDED_LISTING_STATUSES = (ListingStatus.EXPIRED, ListingStatus.CLOSED)
ON_THE_WAY = (AssignmentStatus.EN_ROUTE_PICKUP, AssignmentStatus.COLLECTED, AssignmentStatus.EN_ROUTE_DROPOFF,
              AssignmentStatus.DELIVERED)
RECENT_NOTIFICATIONS = 5


def _count(db: Session, query: Select) -> int:
    return db.scalar(select(func.count()).select_from(query.subquery())) or 0


def _notifications(db: Session, user: User) -> dict[str, Any]:
    page = notification_service.list_mine(db, user, unread_only=False, category=None, page=1,
                                          page_size=RECENT_NOTIFICATIONS)
    return {"unread_notifications": notification_service.unread_count(db, user),
            "recent_notifications": page["items"]}


def _zeros(fields: list[str]) -> dict[str, int]:
    return dict.fromkeys(fields, 0)


# ---------------------------------------------------------------------- provider


def provider(db: Session, user: User) -> dict[str, Any]:
    base = {"role": "provider", **_notifications(db, user)}
    profile_id = db.scalar(select(ProviderProfile.id).where(ProviderProfile.user_id == user.id))
    if profile_id is None:
        return {**base, "profile_complete": False, **_zeros([
            "total_listings", "active_listings", "urgent_listings", "pending_requests", "approved_requests",
            "completed_distributions", "expired_or_cancelled_listings"])}
    mine = select(FoodListing.id).where(FoodListing.provider_id == profile_id)
    requests = select(FoodRequest.id).join(FoodListing, FoodListing.id == FoodRequest.listing_id).where(
        FoodListing.provider_id == profile_id)
    return {
        **base, "profile_complete": True,
        "total_listings": _count(db, mine.where(FoodListing.status != ListingStatus.REMOVED)),
        "active_listings": _count(db, mine.where(FoodListing.status == ListingStatus.ACTIVE)),
        "urgent_listings": _count(db, mine.where(FoodListing.status == ListingStatus.ACTIVE, FoodListing.urgency_level.in_(
            (UrgencyLevel.HIGH, UrgencyLevel.CRITICAL)))),
        "pending_requests": _count(db, requests.where(FoodRequest.status == RequestStatus.PENDING)),
        "approved_requests": _count(db, requests.where(FoodRequest.status.in_(APPROVED_STATUSES))),
        "completed_distributions": _count(db, select(Collection.id).join(Allocation).join(
            FoodListing, FoodListing.id == Allocation.listing_id).where(
            FoodListing.provider_id == profile_id, Collection.status == CollectionStatus.RECEIVED)),
        "expired_or_cancelled_listings": _count(db, mine.where(FoodListing.status.in_(ENDED_LISTING_STATUSES))),
    }


# ---------------------------------------------------------------------- receiver


def receiver(db: Session, user: User) -> dict[str, Any]:
    base = {"role": "receiver", **_notifications(db, user)}
    profile_id = db.scalar(select(ReceiverProfile.id).where(ReceiverProfile.user_id == user.id))
    if profile_id is None:
        return {**base, "profile_complete": False, **_zeros([
            "available_food", "active_requests", "pending_requests", "approved_requests", "rejected_requests",
            "completed_collections"])}
    mine = select(FoodRequest.id).where(FoodRequest.receiver_id == profile_id)
    return {
        **base, "profile_complete": True,
        # the same rules as the browse page, inside the receiver's own pickup radius
        "available_food": browse_service.browse(db, user, page=1, page_size=1)["total"],
        "active_requests": _count(db, mine.where(FoodRequest.status.in_(ACTIVE_REQUEST_STATUSES))),
        "pending_requests": _count(db, mine.where(FoodRequest.status == RequestStatus.PENDING)),
        "approved_requests": _count(db, mine.where(FoodRequest.status.in_(APPROVED_STATUSES))),
        "rejected_requests": _count(db, mine.where(FoodRequest.status == RequestStatus.REJECTED)),
        "completed_collections": _count(db, select(Collection.id).join(Allocation).where(
            Allocation.receiver_id == profile_id, Collection.status == CollectionStatus.RECEIVED)),
    }


# --------------------------------------------------------------------- volunteer


def volunteer(db: Session, user: User) -> dict[str, Any]:
    from app.services import assignment_service  # local: assignment_service imports many services

    base = {"role": "volunteer", **_notifications(db, user)}
    profile_id = db.scalar(select(VolunteerProfile.id).where(VolunteerProfile.user_id == user.id))
    if profile_id is None:
        return {**base, "profile_complete": False, **_zeros([
            "available_assignments", "assigned_tasks", "accepted_tasks", "active_collections",
            "completed_deliveries"])}
    mine = select(VolunteerAssignment.id).where(VolunteerAssignment.volunteer_id == profile_id)
    return {
        **base, "profile_complete": True,
        "available_assignments": len(assignment_service.open_tasks(db, user)),
        "assigned_tasks": _count(db, mine.where(VolunteerAssignment.status == AssignmentStatus.ASSIGNED)),
        "accepted_tasks": _count(db, mine.where(VolunteerAssignment.status == AssignmentStatus.ACCEPTED)),
        "active_collections": _count(db, mine.where(VolunteerAssignment.status.in_(ON_THE_WAY))),
        "completed_deliveries": _count(db, mine.where(VolunteerAssignment.status == AssignmentStatus.COMPLETED)),
    }


# ------------------------------------------------------------------------- admin


def _by_unit(db: Session, quantity, query: Select) -> list[dict[str, Any]]:  # noqa: ANN001
    rows = db.execute(query.with_only_columns(FoodListing.unit, func.sum(quantity)).group_by(FoodListing.unit)
                      .order_by(FoodListing.unit)).all()
    return [{"unit": unit, "quantity": total} for unit, total in rows if total]


def admin(db: Session, user: User) -> dict[str, Any]:
    users = select(User.id).where(User.deleted_at.is_(None))
    listings = select(FoodListing.id).where(FoodListing.status != ListingStatus.REMOVED)
    requests = select(FoodRequest.id)
    collections = select(FoodListing.unit).select_from(Collection).join(Allocation).join(
        FoodListing, FoodListing.id == Allocation.listing_id)
    return {
        "role": "admin", "profile_complete": True, **_notifications(db, user),
        "total_users": _count(db, users),
        "total_providers": _count(db, users.where(User.role == UserRole.PROVIDER)),
        "total_receivers": _count(db, users.where(User.role == UserRole.RECEIVER)),
        "total_volunteers": _count(db, users.where(User.role == UserRole.VOLUNTEER)),
        "pending_verifications": _count(db, select(VerificationRequest.id).where(
            VerificationRequest.status == VerificationRequestStatus.PENDING)),
        "total_listings": _count(db, listings),
        "active_listings": _count(db, listings.where(FoodListing.status == ListingStatus.ACTIVE)),
        "total_requests": _count(db, requests),
        "pending_requests": _count(db, requests.where(FoodRequest.status == RequestStatus.PENDING)),
        "approved_requests": _count(db, requests.where(FoodRequest.status.in_(APPROVED_STATUSES))),
        "completed_distributions": _count(db, select(Collection.id).where(
            Collection.status == CollectionStatus.RECEIVED)),
        # plan 3.5: listed -> allocated -> handed over ("collected") -> received ("distributed")
        "quantity_listed": _by_unit(db, FoodListing.total_quantity, select(FoodListing.unit).where(
            FoodListing.status != ListingStatus.REMOVED)),
        "quantity_allocated": _by_unit(db, Allocation.approved_quantity, select(FoodListing.unit).select_from(
            Allocation).join(FoodListing, FoodListing.id == Allocation.listing_id).where(
            Allocation.status != AllocationStatus.CANCELLED)),
        "quantity_collected": _by_unit(db, Collection.handed_over_quantity, collections),
        "quantity_distributed": _by_unit(db, Collection.received_quantity, collections),
        "expired_and_cancelled_listings": _count(db, listings.where(FoodListing.status.in_(ENDED_LISTING_STATUSES))),
        "recent_activity": activity(db, limit=10),
    }


def for_user(db: Session, user: User) -> dict[str, Any]:
    return {UserRole.PROVIDER: provider, UserRole.RECEIVER: receiver, UserRole.VOLUNTEER: volunteer,
            UserRole.ADMIN: admin}[user.role](db, user)


# ------------------------------------------------------------------ activity feed


def _item(at: datetime, kind: str, summary: str, entity_type: str | None, entity_id: uuid.UUID | None) -> dict:
    return {"at": at, "kind": kind, "summary": summary, "entity_type": entity_type, "entity_id": entity_id}


def _status(from_status: str | None, to_status: str) -> str:
    return to_status.replace("_", " ") if from_status is None else \
        f"{from_status.replace('_', ' ')} → {to_status.replace('_', ' ')}"


def activity(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    """Recent platform activity, newest first: registrations, listings, requests, collections,
    deliveries and administrator actions. Each source gives its newest `limit` rows; they are merged."""
    items: list[dict] = []

    for u in db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc()).limit(limit)):
        items.append(_item(u.created_at, "user.registered", f"{u.full_name} registered as a {u.role.value}",
                           "user", u.id))

    rows = db.execute(select(ListingStatusHistory, FoodListing).join(
        FoodListing, FoodListing.id == ListingStatusHistory.listing_id)
        .order_by(ListingStatusHistory.created_at.desc()).limit(limit)).unique().all()
    for h, listing in rows:
        if h.from_status is None:
            summary = (f"{listing.provider.business_name} listed {listing.food_name} "
                       f"({format_quantity(listing.total_quantity, listing.unit)})")
            items.append(_item(h.created_at, "listing.created", summary, "listing", listing.id))
        else:
            items.append(_item(h.created_at, "listing.status", f"Listing {listing.food_name}: "
                               f"{_status(h.from_status, h.to_status)}", "listing", listing.id))

    rows = db.execute(select(RequestStatusHistory, FoodRequest).join(
        FoodRequest, FoodRequest.id == RequestStatusHistory.request_id)
        .order_by(RequestStatusHistory.created_at.desc()).limit(limit)).unique().all()
    for h, r in rows:
        if h.from_status is None:
            summary = (f"{r.receiver.receiver_name} requested "
                       f"{format_quantity(r.requested_quantity, r.listing.unit)} of {r.listing.food_name}")
            items.append(_item(h.created_at, "request.created", summary, "request", r.id))
        else:
            items.append(_item(h.created_at, "request.status", f"Request by {r.receiver.receiver_name} for "
                               f"{r.listing.food_name}: {_status(h.from_status, h.to_status)}", "request", r.id))

    for c in db.scalars(select(Collection).where(Collection.status.in_((CollectionStatus.RECEIVED, CollectionStatus.FAILED)))
                        .order_by(Collection.updated_at.desc()).limit(limit)).unique():
        listing = c.allocation.listing
        if c.status == CollectionStatus.RECEIVED:
            summary = (f"{c.allocation.receiver.receiver_name} received "
                       f"{format_quantity(c.received_quantity, listing.unit)} of {listing.food_name}")
            items.append(_item(c.received_at or c.updated_at, "collection.received", summary, "collection", c.id))
        else:
            items.append(_item(c.updated_at, "collection.failed", f"Collection of {listing.food_name} for "
                               f"{c.allocation.receiver.receiver_name} failed", "collection", c.id))

    rows = db.execute(select(AssignmentStatusHistory, VolunteerAssignment).join(
        VolunteerAssignment, VolunteerAssignment.id == AssignmentStatusHistory.assignment_id)
        .where(AssignmentStatusHistory.from_status.is_not(None))
        .order_by(AssignmentStatusHistory.created_at.desc()).limit(limit)).unique().all()
    for h, task in rows:
        who = task.volunteer.user.full_name if task.volunteer is not None else "Volunteer task"
        items.append(_item(h.created_at, "assignment.status", f"{who}: delivery of "
                           f"{task.collection.allocation.listing.food_name} {_status(h.from_status, h.to_status)}",
                           "assignment", task.id))

    for a in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)):
        summary = f"{a.actor_email or 'System'}: {a.action}" + (f" ({a.reason})" if a.reason else "")
        items.append(_item(a.created_at, "admin.action", summary, a.entity_type, a.entity_id))

    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:limit]
