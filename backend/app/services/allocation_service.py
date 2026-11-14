"""Allocation workflow (spec 5.5 and 6.4, Technical Plan sections 2.3 and 7.5).

  1. suggest  -> run the pure engine on the PENDING requests; save a DRAFT run with a
                 snapshot of every input and an explanation per line
  2. confirm  -> the provider/admin accepts the suggestion, optionally changing lines
                 (each change needs a written reason). Runs in ONE transaction with the
                 listing row LOCKED, and re-checks everything first.
  3. discard  -> throw a draft away
  4. cancel   -> undo one confirmed allocation (the quantity returns to the listing)
  approve shortcut -> a one-request run confirmed immediately (plan 4.5)

Who: the provider who owns the listing, or an administrator (spec: "authorized
provider or administrator reviews and confirms").
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    COUNTABLE_UNITS,
    AccountStatus,
    AllocationRunPolicy,
    AllocationRunStatus,
    AllocationStatus,
    ListingStatus,
    RequestStatus,
    UserRole,
)
from app.domain.formatting import format_quantity
from app.domain.state_machines import check_request_transition
from app.engines.allocation import Candidate, Policy, allocate
from app.models.allocation import Allocation, AllocationRun, AllocationRunLine
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import ProviderProfile, ReceiverProfile
from app.models.request import FoodRequest, RequestStatusHistory
from app.models.user import User
from app.services import collection_service, urgency_service
from app.services.audit_service import ClientInfo, record

OPEN_FOR_ALLOCATION = (ListingStatus.ACTIVE, ListingStatus.PAUSED)


def _now() -> datetime:
    return datetime.now(UTC)


def _listing_for(db: Session, user: User, listing_id: uuid.UUID, *, lock: bool = False) -> FoodListing:
    query = select(FoodListing).where(FoodListing.id == listing_id)
    if lock:
        query = query.with_for_update(of=FoodListing)
    listing = db.scalar(query)
    if listing is None or listing.status == ListingStatus.REMOVED or not (
        user.role == UserRole.ADMIN or listing.provider.user_id == user.id
    ):
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    return listing


def _ensure_open(db: Session, listing: FoodListing) -> None:
    urgency_service.attach(db, [listing])
    if listing.status not in OPEN_FOR_ALLOCATION or listing.urgency.is_past_deadline:
        raise AppError(409, "LISTING_NOT_AVAILABLE", "This listing can no longer be allocated.",
                       {"listing_status": listing.status.value})


def _receiver_can_receive(profile: ReceiverProfile) -> bool:
    user = profile.user
    return (profile.is_eligible and user.deleted_at is None and user.account_status == AccountStatus.ACTIVE
            and user.is_verified)


def _candidate(request: FoodRequest, listing: FoodListing) -> Candidate:
    profile = request.receiver
    preference = next((p for p in profile.food_preferences
                       if p.food_category_id == listing.food_category_id and p.unit == listing.unit), None)
    return Candidate(
        request_id=request.id, requested=request.requested_quantity, weight=profile.category.priority_weight,
        requested_at=request.created_at, capacity=preference.max_quantity_per_pickup if preference else None,
        eligible=_receiver_can_receive(profile), label=profile.category.name,
    )


def _supersede_drafts(db: Session, listing_id: uuid.UUID) -> None:
    db.execute(update(AllocationRun).where(AllocationRun.listing_id == listing_id,
                                           AllocationRun.status == AllocationRunStatus.DRAFT)
               .values(status=AllocationRunStatus.SUPERSEDED))


def _build_run(db: Session, user: User, listing: FoodListing, requests: list[FoodRequest],
               policy: AllocationRunPolicy) -> AllocationRun:
    candidates = [_candidate(r, listing) for r in requests]
    engine_policy = Policy.PRIORITY_WEIGHTED if policy == AllocationRunPolicy.MANUAL else Policy(policy.value)
    result = allocate(candidates, listing.remaining_quantity, engine_policy, listing.unit,
                      whole_units=listing.unit in COUNTABLE_UNITS)
    snapshot = {
        "listing": {"id": str(listing.id), "unit": listing.unit.value, "total": str(listing.total_quantity),
                    "allocated": str(listing.allocated_quantity), "version": listing.version},
        "candidates": [{"request_id": str(c.request_id), "requested": str(c.requested), "weight": str(c.weight),
                        "capacity": str(c.capacity) if c.capacity is not None else None,
                        "eligible": c.eligible, "requested_at": c.requested_at.isoformat(), "category": c.label}
                       for c in candidates],
    }
    run = AllocationRun(listing_id=listing.id, policy=policy, available_snapshot=listing.remaining_quantity,
                        listing_version=listing.version, input_snapshot=snapshot, created_by=user.id)
    by_id = {c.request_id: c for c in candidates}
    run.lines = [AllocationRunLine(request_id=line.request_id, position=i, requested_quantity=line.requested,
                                   cap_quantity=line.cap, priority_weight=by_id[line.request_id].weight,
                                   suggested_quantity=line.suggested, explanation=line.explanation)
                 for i, line in enumerate(result.lines)]
    db.add(run)
    return run


def _pending(db: Session, listing_id: uuid.UUID) -> list[FoodRequest]:
    return list(db.scalars(select(FoodRequest).where(
        FoodRequest.listing_id == listing_id, FoodRequest.status == RequestStatus.PENDING)
        .order_by(FoodRequest.created_at, FoodRequest.id)).unique())


# ------------------------------------------------------------------- suggest


def suggest(db: Session, user: User, listing_id: uuid.UUID, policy: AllocationRunPolicy) -> AllocationRun:
    if policy == AllocationRunPolicy.MANUAL:
        raise AppError(422, "VALIDATION_ERROR", "Choose priority_weighted, proportional_fair or first_come.")
    listing = _listing_for(db, user, listing_id)
    _ensure_open(db, listing)
    requests = _pending(db, listing.id)
    if not requests:
        raise AppError(409, "NO_PENDING_REQUESTS", "There are no pending requests to allocate.")
    _supersede_drafts(db, listing.id)  # only the newest suggestion can be confirmed
    run = _build_run(db, user, listing, requests, policy)
    db.commit()
    db.refresh(run)
    return run


# ------------------------------------------------------------------- confirm


def _stale(db: Session, run: AllocationRun, message: str) -> AppError:
    run.status = AllocationRunStatus.SUPERSEDED
    db.commit()
    return AppError(409, "RUN_STALE", message + " Please generate a new suggestion.")


def _ensure_within_available(total: Decimal, listing: FoodListing) -> None:
    if total > listing.remaining_quantity:
        raise AppError(422, "EXCEEDS_AVAILABLE",
                       f"Only {format_quantity(listing.remaining_quantity, listing.unit)} available, "
                       f"but {format_quantity(total, listing.unit)} were approved.")


def confirm(db: Session, user: User, run_id: uuid.UUID, overrides: dict[uuid.UUID, tuple[Decimal, str | None]],
            client: ClientInfo) -> AllocationRun:
    run = db.scalar(select(AllocationRun).where(AllocationRun.id == run_id).with_for_update(of=AllocationRun))
    if run is None:
        raise AppError(404, "NOT_FOUND", "Allocation suggestion not found.")
    # Lock the listing: concurrent confirms / edits of this listing now wait for us (plan issue H5).
    listing = _listing_for(db, user, run.listing_id, lock=True)
    if run.status != AllocationRunStatus.DRAFT:
        raise AppError(409, "RUN_NOT_DRAFT", f"This suggestion is already {run.status.value}.",
                       {"status": run.status.value})
    _ensure_open(db, listing)

    # Re-validate: has anything changed since the suggestion was made?
    if listing.version != run.listing_version or listing.remaining_quantity != run.available_snapshot:
        raise _stale(db, run, "The listing changed after this suggestion was made.")
    requests = {r.id: r for r in db.scalars(select(FoodRequest).where(
        FoodRequest.id.in_([line.request_id for line in run.lines])).with_for_update(of=FoodRequest)).unique()}
    if any(requests[line.request_id].status != RequestStatus.PENDING for line in run.lines):
        raise _stale(db, run, "Some requests were cancelled or decided after this suggestion was made.")

    unknown = set(overrides) - {line.request_id for line in run.lines}
    if unknown:
        raise AppError(422, "UNKNOWN_REQUEST", "A changed line refers to a request that is not in this suggestion.",
                       {"request_ids": sorted(str(u) for u in unknown)})

    whole = listing.unit in COUNTABLE_UNITS
    decisions: list[tuple[AllocationRunLine, Decimal, str | None]] = []
    for line in run.lines:
        approved, reason = overrides.get(line.request_id, (line.suggested_quantity, None))
        approved = Decimal(approved)
        field = {"request_id": str(line.request_id)}
        if approved < 0 or approved > line.requested_quantity:
            raise AppError(422, "INVALID_APPROVED_QUANTITY",
                           "An approved quantity must be between 0 and the requested quantity.", field)
        if whole and approved % 1 != 0:
            raise AppError(422, "WHOLE_NUMBER_REQUIRED",
                           f"Quantities in '{listing.unit.value}' must be whole numbers.", field)
        if approved > 0 and not _receiver_can_receive(requests[line.request_id].receiver):
            raise AppError(422, "INELIGIBLE_RECEIVER", "This receiver is currently not eligible to receive food.", field)
        if approved != line.suggested_quantity:
            reason = " ".join((reason or "").split())
            if len(reason) < 5:
                # Spec 4.4.7: "override recommendations with a recorded reason".
                raise AppError(422, "OVERRIDE_REASON_REQUIRED",
                               "Please give a reason for changing the suggested quantity.", field)
        else:
            reason = None
        decisions.append((line, approved, reason))

    total = sum((approved for _, approved, _ in decisions), Decimal(0))
    _ensure_within_available(total, listing)
    if total == 0:
        raise AppError(422, "NOTHING_APPROVED",
                       "Nothing would be allocated. To decline the requests, reject them instead.")

    now = _now()
    for line, approved, reason in decisions:
        request = requests[line.request_id]
        if approved > 0:
            new_status = (RequestStatus.APPROVED if approved == request.requested_quantity
                          else RequestStatus.PARTIALLY_APPROVED)
            allocation = Allocation(run_id=run.id, listing_id=listing.id, request_id=request.id,
                                    receiver_id=request.receiver_id, suggested_quantity=line.suggested_quantity,
                                    approved_quantity=approved, decision_reason=line.explanation,
                                    override_reason=reason, confirmed_by=user.id)
            db.add(allocation)
            # Every confirmed allocation gets its collection record (pickup or volunteer delivery).
            collection_service.create_for_allocation(db, allocation, request)
            note = reason or line.explanation
        else:
            new_status = RequestStatus.REJECTED
            note = reason or line.explanation
            request.rejection_reason = note
        check_request_transition(request.status, new_status)
        db.add(RequestStatusHistory(request_id=request.id, from_status=request.status.value,
                                    to_status=new_status.value, changed_by=user.id, reason=note))
        request.status = new_status
        request.responded_at = now
        request.version += 1

    listing.allocated_quantity += total
    listing.version += 1
    if listing.remaining_quantity == 0 and listing.status == ListingStatus.ACTIVE:
        db.add(ListingStatusHistory(listing_id=listing.id, from_status=listing.status.value,
                                    to_status=ListingStatus.FULLY_ALLOCATED.value, changed_by=user.id,
                                    reason="all food allocated"))
        listing.status = ListingStatus.FULLY_ALLOCATED

    run.status = AllocationRunStatus.CONFIRMED
    run.confirmed_by = user.id
    run.confirmed_at = now

    overridden = [(line, approved, reason) for line, approved, reason in decisions if reason]
    if user.role == UserRole.ADMIN or overridden:
        record(db, actor=user, action="allocation.confirm", entity_type="food_listing", entity_id=listing.id,
               client=client, reason="; ".join(r for _, _, r in overridden) or None,
               after={"run_id": str(run.id), "total_approved": str(total),
                      "overrides": [{"request_id": str(line.request_id), "suggested": str(line.suggested_quantity),
                                     "approved": str(approved), "reason": reason}
                                    for line, approved, reason in overridden]})
    try:
        db.commit()
    except IntegrityError as exc:
        # Last line of defence: the database itself refuses over-allocation
        # (CHECK allocated <= total) or a second allocation for the same request.
        db.rollback()
        raise AppError(409, "ALLOCATION_CONFLICT",
                       "This allocation conflicts with other changes to the listing. Please generate a new suggestion."
                       ) from exc
    db.refresh(run)
    return run


def discard(db: Session, user: User, run_id: uuid.UUID) -> AllocationRun:
    run = get_run(db, user, run_id)
    if run.status != AllocationRunStatus.DRAFT:
        raise AppError(409, "RUN_NOT_DRAFT", f"This suggestion is already {run.status.value}.")
    run.status = AllocationRunStatus.DISCARDED
    db.commit()
    db.refresh(run)
    return run


def approve_request(db: Session, user: User, request_id: uuid.UUID, quantity: Decimal | None,
                    reason: str | None, client: ClientInfo) -> Allocation:
    """Shortcut for the common case of approving one request (plan 4.5): internally a
    one-line allocation run, so the same rules, records and audit apply."""
    request = db.get(FoodRequest, request_id)
    if request is None:
        raise AppError(404, "NOT_FOUND", "Request not found.")
    listing = _listing_for(db, user, request.listing_id)
    if request.status != RequestStatus.PENDING:
        raise AppError(409, "INVALID_STATUS_TRANSITION", f"A '{request.status.value}' request can't be approved.")
    _ensure_open(db, listing)
    _supersede_drafts(db, listing.id)
    run = _build_run(db, user, listing, [request], AllocationRunPolicy.MANUAL)
    db.commit()
    overrides = {} if quantity is None else {request.id: (quantity, reason)}
    confirm(db, user, run.id, overrides, client)
    return db.scalar(select(Allocation).where(Allocation.request_id == request.id))


# -------------------------------------------------------------------- cancel


def cancel_allocation(db: Session, user: User, allocation_id: uuid.UUID, reason: str,
                      client: ClientInfo) -> Allocation:
    allocation = db.scalar(select(Allocation).where(Allocation.id == allocation_id).with_for_update(of=Allocation))
    if allocation is None:
        raise AppError(404, "NOT_FOUND", "Allocation not found.")
    listing = _listing_for(db, user, allocation.listing_id, lock=True)
    if allocation.status != AllocationStatus.CONFIRMED:
        raise AppError(409, "ALLOCATION_NOT_CANCELLABLE",
                       f"A '{allocation.status.value}' allocation can't be cancelled.")
    request = db.get(FoodRequest, allocation.request_id)
    check_request_transition(request.status, RequestStatus.CANCELLED)
    collection_service.cancel_with_allocation(db, allocation)  # refuses if already handed over

    allocation.status = AllocationStatus.CANCELLED
    allocation.cancelled_reason = reason
    listing.allocated_quantity -= allocation.approved_quantity  # the food is available again
    listing.version += 1
    if listing.status == ListingStatus.FULLY_ALLOCATED and listing.remaining_quantity > 0:
        urgency_service.attach(db, [listing])
        if not listing.urgency.is_past_deadline:
            db.add(ListingStatusHistory(listing_id=listing.id, from_status=listing.status.value,
                                        to_status=ListingStatus.ACTIVE.value, changed_by=user.id,
                                        reason="an allocation was cancelled"))
            listing.status = ListingStatus.ACTIVE
    db.add(RequestStatusHistory(request_id=request.id, from_status=request.status.value,
                                to_status=RequestStatus.CANCELLED.value, changed_by=user.id, reason=reason))
    request.status = RequestStatus.CANCELLED
    request.version += 1
    record(db, actor=user, action="allocation.cancel", entity_type="allocation", entity_id=allocation.id,
           client=client, reason=reason, before={"status": "confirmed", "approved": str(allocation.approved_quantity)},
           after={"status": "cancelled"})
    db.commit()
    db.refresh(allocation)
    return allocation


# ------------------------------------------------------------------- reading


def get_run(db: Session, user: User, run_id: uuid.UUID) -> AllocationRun:
    run = db.get(AllocationRun, run_id)
    if run is None:
        raise AppError(404, "NOT_FOUND", "Allocation suggestion not found.")
    _listing_for(db, user, run.listing_id)  # 404 unless owner or admin
    return run


def runs_for_listing(db: Session, user: User, listing_id: uuid.UUID) -> list[AllocationRun]:
    _listing_for(db, user, listing_id)
    return list(db.scalars(select(AllocationRun).where(AllocationRun.listing_id == listing_id)
                           .order_by(AllocationRun.created_at.desc(), AllocationRun.id)))


def _scoped_allocations(user: User):  # noqa: ANN202
    query = select(Allocation)
    if user.role == UserRole.RECEIVER:
        return query.join(ReceiverProfile, ReceiverProfile.id == Allocation.receiver_id).where(
            ReceiverProfile.user_id == user.id)
    if user.role == UserRole.PROVIDER:
        return query.join(FoodListing, FoodListing.id == Allocation.listing_id).join(
            ProviderProfile, ProviderProfile.id == FoodListing.provider_id).where(ProviderProfile.user_id == user.id)
    if user.role == UserRole.ADMIN:
        return query
    raise AppError(403, "FORBIDDEN", "Volunteers see their tasks in the assignments section.")


def list_allocations(db: Session, user: User, *, listing_id: uuid.UUID | None, status: AllocationStatus | None,
                     page: int, page_size: int) -> dict[str, Any]:
    query = _scoped_allocations(user)
    if listing_id is not None:
        query = query.where(Allocation.listing_id == listing_id)
    if status is not None:
        query = query.where(Allocation.status == status)
    total = db.scalar(select(func.count()).select_from(query.with_only_columns(Allocation.id).subquery()))
    items = db.scalars(query.order_by(Allocation.created_at.desc(), Allocation.id)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_allocation(db: Session, user: User, allocation_id: uuid.UUID) -> Allocation:
    allocation = db.scalar(_scoped_allocations(user).where(Allocation.id == allocation_id))
    if allocation is None:
        raise AppError(404, "NOT_FOUND", "Allocation not found.")
    return allocation
