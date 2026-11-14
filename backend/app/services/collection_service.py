"""Collection: handover and receipt of allocated food (spec 4.1.5, 4.2.5, 5.7).

Workflow:
  allocation confirmed -> collection SCHEDULED (created automatically)
  provider hands the food over  -> HANDED_OVER   (allocation: collected)
  receiver confirms receipt     -> RECEIVED      (allocation: completed, request: completed,
                                                   match outcome: completed)  = redistribution done
  something went wrong          -> FAILED        (with a reason; food not yet handed over goes
                                                   back on offer)

Who: the provider of the listing, the receiver of the allocation, administrators.
Contact details of both sides are shown to both sides here: this is the "authorized
transaction" in which spec section 15 allows sharing them.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    COUNTABLE_UNITS,
    AllocationStatus,
    CollectionMethod,
    CollectionPreference,
    CollectionStatus,
    ListingStatus,
    MatchOutcome,
    RequestStatus,
    UserRole,
)
from app.domain.formatting import format_quantity
from app.domain.state_machines import check_collection_transition, check_request_transition
from app.models.allocation import Allocation
from app.models.collection import Collection
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import ProviderProfile, ReceiverProfile
from app.models.request import FoodRequest, RequestStatusHistory
from app.models.user import User
from app.services import assignment_service, urgency_service
from app.services.audit_service import ClientInfo, record


def _now() -> datetime:
    return datetime.now(UTC)


def create_for_allocation(db: Session, allocation: Allocation, request: FoodRequest) -> Collection:
    """Called when an allocation is confirmed. The caller commits."""
    method = (CollectionMethod.VOLUNTEER if request.collection_preference == CollectionPreference.NEEDS_VOLUNTEER
              else CollectionMethod.SELF_PICKUP)
    collection = Collection(allocation=allocation, method=method, scheduled_pickup_at=request.preferred_pickup_at)
    db.add(collection)
    if method == CollectionMethod.VOLUNTEER:
        db.flush()
        assignment_service.create_open(db, collection)
    return collection


# ------------------------------------------------------------------ access


def _is_provider(user: User, c: Collection) -> bool:
    return c.provider.user_id == user.id


def _is_receiver(user: User, c: Collection) -> bool:
    return c.receiver.user_id == user.id


def _involved_for_update(db: Session, user: User, collection_id: uuid.UUID) -> Collection:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id).with_for_update(of=Collection))
    if collection is None or not (user.role == UserRole.ADMIN or _is_provider(user, collection)
                                  or _is_receiver(user, collection)):
        raise AppError(404, "NOT_FOUND", "Collection not found.")
    return collection


def _quantity(value: Decimal, collection: Collection, field: str, limit: Decimal, limit_text: str) -> Decimal:
    unit = collection.listing.unit
    if value <= 0:
        raise AppError(422, "INVALID_QUANTITY", "The quantity must be more than 0.", {"field": field})
    if unit in COUNTABLE_UNITS and value % 1 != 0:
        raise AppError(422, "WHOLE_NUMBER_REQUIRED", f"Quantity in '{unit.value}' must be a whole number.",
                       {"field": field})
    if value > limit:
        raise AppError(422, "QUANTITY_TOO_HIGH",
                       f"The quantity can't be more than {format_quantity(limit, unit)} ({limit_text}).",
                       {"field": field, "limit": float(limit)})
    return value


def _request_history(db: Session, request: FoodRequest, target: RequestStatus, user: User, reason: str) -> None:
    check_request_transition(request.status, target)
    db.add(RequestStatusHistory(request_id=request.id, from_status=request.status.value, to_status=target.value,
                                changed_by=user.id, reason=reason))
    request.status = target
    request.version += 1


def _return_to_listing(db: Session, listing: FoodListing, quantity: Decimal, user: User, reason: str) -> None:
    """Food that never left the provider becomes available again."""
    listing.allocated_quantity -= quantity
    listing.version += 1
    if listing.status == ListingStatus.FULLY_ALLOCATED and listing.remaining_quantity > 0:
        urgency_service.attach(db, [listing])
        if not listing.urgency.is_past_deadline:
            db.add(ListingStatusHistory(listing_id=listing.id, from_status=listing.status.value,
                                        to_status=ListingStatus.ACTIVE.value, changed_by=user.id, reason=reason))
            listing.status = ListingStatus.ACTIVE


# ---------------------------------------------------------------- actions


def schedule(db: Session, user: User, collection_id: uuid.UUID, method: CollectionMethod | None,
             scheduled_pickup_at: datetime | None) -> Collection:
    collection = _involved_for_update(db, user, collection_id)
    if collection.status != CollectionStatus.SCHEDULED:
        raise AppError(409, "COLLECTION_NOT_EDITABLE",
                       f"A '{collection.status.value}' collection can't be rescheduled.")
    listing = collection.listing
    if scheduled_pickup_at is not None and not (
        listing.pickup_window_start <= scheduled_pickup_at <= listing.collection_deadline
    ):
        raise AppError(422, "OUTSIDE_PICKUP_WINDOW",
                       "The pickup time must be between the start of the pickup window and the collection deadline.",
                       {"field": "scheduled_pickup_at"})
    if method is not None and method != collection.method:
        if method == CollectionMethod.VOLUNTEER:
            assignment_service.create_open(db, collection, user.id)
        else:
            assignment_service.cancel_active(db, collection.id, user.id, "switched to self pickup")
        collection.method = method
    if scheduled_pickup_at is not None:
        collection.scheduled_pickup_at = scheduled_pickup_at
    collection.version += 1
    db.commit()
    db.refresh(collection)
    return collection


def perform_handover(db: Session, collection: Collection, user: User, quantity: Decimal, notes: str | None) -> None:
    """The handover rules, shared by the provider and by a volunteer collecting the food.
    No permission check and no commit here: the callers do both."""
    check_collection_transition(collection.status, CollectionStatus.HANDED_OVER)
    collection.handed_over_quantity = _quantity(Decimal(quantity), collection, "quantity",
                                                collection.approved_quantity, "the approved quantity")
    collection.handed_over_at = _now()
    collection.handed_over_by = user.id
    collection.status = CollectionStatus.HANDED_OVER
    collection.notes = notes
    collection.version += 1
    collection.allocation.status = AllocationStatus.COLLECTED


def handover(db: Session, user: User, collection_id: uuid.UUID, quantity: Decimal, notes: str | None,
             client: ClientInfo) -> Collection:
    collection = _involved_for_update(db, user, collection_id)
    if not (_is_provider(user, collection) or user.role == UserRole.ADMIN):
        raise AppError(403, "FORBIDDEN", "Only the provider confirms the handover.")
    perform_handover(db, collection, user, quantity, notes)
    assignment_service.on_provider_handover(db, collection.id, user.id)
    if user.role == UserRole.ADMIN:
        record(db, actor=user, action="collection.handover", entity_type="collection", entity_id=collection.id,
               client=client, after={"handed_over_quantity": str(quantity)})
    db.commit()
    db.refresh(collection)
    return collection


def receipt(db: Session, user: User, collection_id: uuid.UUID, quantity: Decimal, notes: str | None,
            reason: str | None, client: ClientInfo) -> Collection:
    collection = _involved_for_update(db, user, collection_id)
    is_admin = user.role == UserRole.ADMIN
    if not (_is_receiver(user, collection) or is_admin):
        raise AppError(403, "FORBIDDEN", "Only the receiver confirms what was received.")
    if is_admin and len(" ".join((reason or "").split())) < 5:
        # Plan 5.4: an administrator may confirm receipt for a receiver, but only with a reason.
        raise AppError(422, "REASON_REQUIRED", "Administrators must give a reason when confirming receipt.",
                       {"field": "reason"})
    check_collection_transition(collection.status, CollectionStatus.RECEIVED)
    collection.received_quantity = _quantity(Decimal(quantity), collection, "quantity",
                                             collection.handed_over_quantity, "the quantity handed over")
    collection.received_at = _now()
    collection.received_by = user.id
    collection.status = CollectionStatus.RECEIVED
    collection.notes = notes or collection.notes
    collection.version += 1
    collection.allocation.status = AllocationStatus.COMPLETED

    request = db.get(FoodRequest, collection.request_id)
    _request_history(db, request, RequestStatus.COMPLETED, user,
                     f"received {format_quantity(collection.received_quantity, collection.listing.unit)}")
    from app.services.matching_service import record_outcome  # circular-import safe here

    record_outcome(db, collection.listing.id, collection.receiver.id, MatchOutcome.COMPLETED)
    assignment_service.on_receipt(db, collection.id, user.id)
    if is_admin:
        record(db, actor=user, action="collection.receipt", entity_type="collection", entity_id=collection.id,
               client=client, reason=reason, after={"received_quantity": str(quantity)})
    db.commit()
    db.refresh(collection)
    return collection


def fail(db: Session, user: User, collection_id: uuid.UUID, reason: str, client: ClientInfo) -> Collection:
    _involved_for_update(db, user, collection_id)  # permission check + lock
    collection = perform_failure(db, collection_id, user, reason, client)
    db.commit()
    db.refresh(collection)
    return collection


def perform_failure(db: Session, collection_id: uuid.UUID, user: User, reason: str, client: ClientInfo) -> Collection:
    """The failure rules, shared by the provider/receiver and by a volunteer. The caller commits."""
    collection = db.scalar(select(Collection).where(Collection.id == collection_id).with_for_update(of=Collection))
    before = collection.status
    check_collection_transition(before, CollectionStatus.FAILED)
    listing = db.scalar(select(FoodListing).where(FoodListing.id == collection.listing.id)
                        .with_for_update(of=FoodListing))
    collection.status = CollectionStatus.FAILED
    collection.failure_reason = reason
    collection.version += 1
    collection.allocation.status = AllocationStatus.FAILED
    if before == CollectionStatus.SCHEDULED:
        _return_to_listing(db, listing, collection.approved_quantity, user, "a collection failed before handover")
    request = db.get(FoodRequest, collection.request_id)
    _request_history(db, request, RequestStatus.CANCELLED, user, f"collection failed: {reason}")
    assignment_service.on_collection_failed(db, collection.id, user.id, reason)
    record(db, actor=user, action="collection.fail", entity_type="collection", entity_id=collection.id,
           client=client, reason=reason, before={"status": before.value}, after={"status": "failed"})
    return collection


def cancel_with_allocation(db: Session, allocation: Allocation) -> None:
    """The allocation is being cancelled: only possible before the handover. The caller commits."""
    collection = db.scalar(select(Collection).where(Collection.allocation_id == allocation.id)
                           .with_for_update(of=Collection))
    if collection is None:
        return
    if collection.status != CollectionStatus.SCHEDULED:
        raise AppError(409, "ALLOCATION_NOT_CANCELLABLE",
                       "The food was already handed over; report a failed collection instead.")
    assignment_service.cancel_active(db, collection.id, None, "allocation cancelled")
    collection.status = CollectionStatus.CANCELLED
    collection.version += 1


# ---------------------------------------------------------------- reading


def _scoped(user: User):  # noqa: ANN202
    query = select(Collection).join(Allocation, Allocation.id == Collection.allocation_id)
    if user.role == UserRole.RECEIVER:
        return query.join(ReceiverProfile, ReceiverProfile.id == Allocation.receiver_id).where(
            ReceiverProfile.user_id == user.id)
    if user.role == UserRole.PROVIDER:
        return query.join(FoodListing, FoodListing.id == Allocation.listing_id).join(
            ProviderProfile, ProviderProfile.id == FoodListing.provider_id).where(ProviderProfile.user_id == user.id)
    if user.role == UserRole.ADMIN:
        return query
    raise AppError(403, "FORBIDDEN", "Volunteers see their deliveries under assignments.")


def list_collections(db: Session, user: User, *, status: CollectionStatus | None, page: int,
                     page_size: int) -> dict[str, Any]:
    query = _scoped(user)
    if status is not None:
        query = query.where(Collection.status == status)
    total = db.scalar(select(func.count()).select_from(query.with_only_columns(Collection.id).subquery()))
    items = db.scalars(query.order_by(Collection.created_at.desc(), Collection.id)
                       .offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_involved(db: Session, user: User, collection_id: uuid.UUID) -> Collection:
    if user.role not in (UserRole.ADMIN, UserRole.PROVIDER, UserRole.RECEIVER):
        raise AppError(404, "NOT_FOUND", "Collection not found.")
    collection = db.scalar(_scoped(user).where(Collection.id == collection_id))
    if collection is None:
        raise AppError(404, "NOT_FOUND", "Collection not found.")
    return collection
