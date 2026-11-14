"""Food request rules (spec 4.2.3-4.2.4, 5.3; Technical Plan section 4.5).

Who can do what:
- create / edit / cancel: the verified, ELIGIBLE receiver who owns the request
- see: that receiver, the provider of the listing, administrators (anyone else: 404)
- reject (with a reason): the provider of the listing, or an administrator (audited)
- approve: done by the allocation stage, which divides the food between requests
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    ACTIVE_REQUEST_STATUSES,
    COUNTABLE_UNITS,
    ListingStatus,
    MatchOutcome,
    RequestStatus,
    UserRole,
)
from app.domain.formatting import format_quantity
from app.domain.state_machines import check_request_transition
from app.engines.geo import haversine_km
from app.models.listing import FoodListing
from app.models.profiles import ProviderProfile, ReceiverProfile
from app.models.request import FoodRequest, RequestStatusHistory
from app.models.user import User
from app.schemas.requests import RequestIn, RequestUpdateIn
from app.services import urgency_service
from app.services.audit_service import ClientInfo, record
from app.services import notification_service as notes


def _now() -> datetime:
    return datetime.now(UTC)


def _field_error(code: str, message: str, field: str, **details: Any) -> AppError:
    return AppError(422, code, message, {"field": field, **details})


def _history(db: Session, request: FoodRequest, from_status: RequestStatus | None, to_status: RequestStatus,
             changed_by: uuid.UUID | None, reason: str | None = None) -> None:
    db.add(RequestStatusHistory(request_id=request.id, from_status=from_status.value if from_status else None,
                                to_status=to_status.value, changed_by=changed_by, reason=reason))


def _receiver_profile(db: Session, user: User) -> ReceiverProfile:
    profile = db.scalar(select(ReceiverProfile).where(ReceiverProfile.user_id == user.id))
    if profile is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your receiver profile first.")
    if not profile.is_eligible:
        raise AppError(403, "RECEIVER_NOT_ELIGIBLE",
                       "Your organization is currently not eligible to request food. Please contact FreshLink support.")
    return profile


def _check_quantity_and_time(db: Session, listing: FoodListing, profile: ReceiverProfile,
                             body: RequestIn | RequestUpdateIn) -> None:
    quantity = Decimal(body.requested_quantity)
    if listing.unit in COUNTABLE_UNITS and quantity % 1 != 0:
        raise _field_error("WHOLE_NUMBER_REQUIRED", f"Quantity in '{listing.unit.value}' must be a whole number.",
                           "requested_quantity")
    remaining = listing.remaining_quantity
    if quantity > remaining:
        raise _field_error("QUANTITY_EXCEEDS_AVAILABLE",
                           f"Only {format_quantity(remaining, listing.unit)} available.",
                           "requested_quantity", available=float(remaining))
    # The receiver's declared capacity for this food category (plan issue H2). It can only
    # be compared when it uses the same unit as the listing.
    preference = next((p for p in profile.food_preferences if p.food_category_id == listing.food_category_id), None)
    if preference is not None and preference.unit == listing.unit and quantity > preference.max_quantity_per_pickup:
        raise _field_error("EXCEEDS_CAPACITY",
                           f"Your profile says you can take at most "
                           f"{format_quantity(preference.max_quantity_per_pickup, listing.unit)} of this food per pickup.",
                           "requested_quantity", capacity=float(preference.max_quantity_per_pickup))
    if body.preferred_pickup_at is not None and not (
        listing.pickup_window_start <= body.preferred_pickup_at <= listing.pickup_window_end
    ):
        raise _field_error("OUTSIDE_PICKUP_WINDOW", "The preferred pickup time must be inside the listing's pickup window.",
                           "preferred_pickup_at")


def _ensure_available(db: Session, listing: FoodListing | None) -> FoodListing:
    if listing is None or listing.status == ListingStatus.REMOVED:
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    urgency_service.attach(db, [listing])
    from app.services.browse_service import active_provider_ids  # local: avoids an import cycle

    provider_active = db.scalar(select(active_provider_ids().where(ProviderProfile.id == listing.provider_id).exists()))
    if (listing.status != ListingStatus.ACTIVE or listing.urgency.is_past_deadline
            or listing.remaining_quantity <= 0 or not provider_active):
        raise AppError(409, "LISTING_NOT_AVAILABLE", "This food is no longer available for requests.",
                       {"listing_status": listing.status.value})
    return listing


def _with_distance(requests: list[FoodRequest]) -> list[FoodRequest]:
    for r in requests:
        r.distance_km = round(haversine_km(r.listing.pickup_latitude, r.listing.pickup_longitude,
                                           r.receiver.latitude, r.receiver.longitude), 2)
    return requests


# ---------------------------------------------------------------------- receiver


def create(db: Session, user: User, body: RequestIn) -> FoodRequest:
    profile = _receiver_profile(db, user)
    listing = _ensure_available(db, db.get(FoodListing, body.listing_id))
    _check_quantity_and_time(db, listing, profile, body)

    duplicate = db.scalar(select(FoodRequest.id).where(
        FoodRequest.listing_id == listing.id, FoodRequest.receiver_id == profile.id,
        FoodRequest.status.in_(ACTIVE_REQUEST_STATUSES)))
    if duplicate:
        raise AppError(409, "DUPLICATE_REQUEST", "You already have an active request for this food. "
                       "Edit that request instead.", {"request_id": str(duplicate)})

    request = FoodRequest(listing_id=listing.id, receiver_id=profile.id,
                          **body.model_dump(exclude={"listing_id"}))
    db.add(request)
    try:
        db.flush()
    except IntegrityError as exc:  # two taps at the same moment: the partial unique index wins
        db.rollback()
        raise AppError(409, "DUPLICATE_REQUEST", "You already have an active request for this food.") from exc
    _history(db, request, None, RequestStatus.PENDING, user.id, "requested")
    # Imported here: matching_service -> browse_service -> urgency_service -> request_service
    # would otherwise be a circular import.
    from app.services.matching_service import record_outcome

    record_outcome(db, listing.id, profile.id, MatchOutcome.REQUESTED)
    notes.notify(db, [listing.provider.user_id], "request_new", "New food request",
                 f"{profile.receiver_name} asked for {format_quantity(request.requested_quantity, listing.unit)} "
                 f"of {listing.food_name}.", "request", request.id)
    db.commit()
    db.refresh(request)
    return _with_distance([request])[0]


def _own_for_update(db: Session, user: User, request_id: uuid.UUID) -> FoodRequest:
    request = db.scalar(select(FoodRequest).where(FoodRequest.id == request_id).with_for_update(of=FoodRequest))
    if request is None or request.receiver.user_id != user.id:
        raise AppError(404, "NOT_FOUND", "Request not found.")
    return request


def update(db: Session, user: User, request_id: uuid.UUID, body: RequestUpdateIn) -> FoodRequest:
    request = _own_for_update(db, user, request_id)
    if request.status != RequestStatus.PENDING:
        raise AppError(409, "REQUEST_NOT_EDITABLE", f"A '{request.status.value}' request can't be edited.")
    profile = _receiver_profile(db, user)
    listing = _ensure_available(db, request.listing)
    _check_quantity_and_time(db, listing, profile, body)
    for key, value in body.model_dump().items():
        setattr(request, key, value)
    request.version += 1
    db.commit()
    db.refresh(request)
    return _with_distance([request])[0]


def cancel(db: Session, user: User, request_id: uuid.UUID, reason: str | None) -> FoodRequest:
    request = _own_for_update(db, user, request_id)
    check_request_transition(request.status, RequestStatus.CANCELLED)
    _history(db, request, request.status, RequestStatus.CANCELLED, user.id, (reason or "").strip() or None)
    request.status = RequestStatus.CANCELLED
    request.version += 1
    notes.notify(db, [request.listing.provider.user_id], "request_update", "Request cancelled",
                 f"{request.receiver.receiver_name} cancelled their request for {request.listing.food_name}.",
                 "request", request.id)
    db.commit()
    db.refresh(request)
    return _with_distance([request])[0]


# ------------------------------------------------------------- provider / admin


def reject(db: Session, user: User, request_id: uuid.UUID, reason: str, client: ClientInfo) -> FoodRequest:
    request = db.scalar(select(FoodRequest).where(FoodRequest.id == request_id).with_for_update(of=FoodRequest))
    is_owner = request is not None and request.listing.provider.user_id == user.id
    if request is None or not (is_owner or user.role == UserRole.ADMIN):
        raise AppError(404, "NOT_FOUND", "Request not found.")
    check_request_transition(request.status, RequestStatus.REJECTED)
    _history(db, request, request.status, RequestStatus.REJECTED, user.id, reason)
    request.status = RequestStatus.REJECTED
    request.rejection_reason = reason
    request.responded_at = _now()
    request.version += 1
    notes.notify(db, [request.receiver.user_id], "request_decision", "Request not approved",
                 f"Your request for {request.listing.food_name} was not approved: {reason}", "request", request.id)
    if user.role == UserRole.ADMIN:  # administrator interventions are audited (spec 4.4.6)
        record(db, actor=user, action="request.reject", entity_type="food_request", entity_id=request.id,
               client=client, reason=reason, before={"status": "pending"}, after={"status": "rejected"})
    db.commit()
    db.refresh(request)
    return _with_distance([request])[0]


def expire_pending_for_listing(db: Session, listing: FoodListing, changed_by: uuid.UUID | None, reason: str) -> int:
    """When a listing closes, is removed or expires, its pending requests can never be
    approved: mark them expired so receivers aren't left waiting. The caller commits."""
    pending = db.scalars(select(FoodRequest).where(
        FoodRequest.listing_id == listing.id, FoodRequest.status == RequestStatus.PENDING)
        .with_for_update(of=FoodRequest)).all()
    for request in pending:
        _history(db, request, RequestStatus.PENDING, RequestStatus.EXPIRED, changed_by, reason)
        request.status = RequestStatus.EXPIRED
        request.responded_at = _now()
        request.version += 1
        notes.notify(db, [request.receiver.user_id], "request_update", "Food no longer available",
                     f"{listing.food_name} is no longer available, so your request has ended.", "request", request.id)
    return len(pending)


def has_any_requests(db: Session, listing_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).select_from(FoodRequest).where(FoodRequest.listing_id == listing_id)) > 0


# ------------------------------------------------------------------------ reading


def _scoped(db: Session, user: User):  # noqa: ANN202
    """The same list endpoint shows different rows per role (plan section 4.1, scoping rule)."""
    query = select(FoodRequest)
    if user.role == UserRole.RECEIVER:
        return query.join(ReceiverProfile, ReceiverProfile.id == FoodRequest.receiver_id).where(
            ReceiverProfile.user_id == user.id)
    if user.role == UserRole.PROVIDER:
        return query.join(FoodListing, FoodListing.id == FoodRequest.listing_id).join(
            ProviderProfile, ProviderProfile.id == FoodListing.provider_id).where(ProviderProfile.user_id == user.id)
    if user.role == UserRole.ADMIN:
        return query
    raise AppError(403, "FORBIDDEN", "Volunteers don't have food requests.")


def list_requests(db: Session, user: User, *, status: RequestStatus | None, listing_id: uuid.UUID | None,
                  page: int, page_size: int) -> dict[str, Any]:
    query = _scoped(db, user)
    if status is not None:
        query = query.where(FoodRequest.status == status)
    if listing_id is not None:
        query = query.where(FoodRequest.listing_id == listing_id)
    total = db.scalar(select(func.count()).select_from(query.with_only_columns(FoodRequest.id).subquery()))
    items = db.scalars(query.order_by(FoodRequest.created_at.desc(), FoodRequest.id)
                       .offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": _with_distance(list(items)), "total": total, "page": page, "page_size": page_size}


def get_involved(db: Session, user: User, request_id: uuid.UUID) -> FoodRequest:
    request = db.get(FoodRequest, request_id)
    involved = request is not None and (
        user.role == UserRole.ADMIN
        or request.receiver.user_id == user.id
        or request.listing.provider.user_id == user.id
    )
    if not involved:
        raise AppError(404, "NOT_FOUND", "Request not found.")
    return _with_distance([request])[0]


def for_listing(db: Session, user: User, listing_id: uuid.UUID, status: RequestStatus | None) -> list[FoodRequest]:
    """Incoming requests for one listing, first come first. (Match scores and reasons are
    added by the matching stage.)"""
    listing = db.get(FoodListing, listing_id)
    if listing is None or not (user.role == UserRole.ADMIN or listing.provider.user_id == user.id):
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    query = select(FoodRequest).where(FoodRequest.listing_id == listing_id)
    if status is not None:
        query = query.where(FoodRequest.status == status)
    return _with_distance(list(db.scalars(query.order_by(FoodRequest.created_at, FoodRequest.id)).unique()))


def history(db: Session, user: User, request_id: uuid.UUID) -> list[RequestStatusHistory]:
    get_involved(db, user, request_id)
    return list(db.scalars(select(RequestStatusHistory).where(RequestStatusHistory.request_id == request_id)
                           .order_by(RequestStatusHistory.created_at, RequestStatusHistory.id)))
