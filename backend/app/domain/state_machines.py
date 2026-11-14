"""Allowed status changes, in ONE place (Technical Plan section 2.2, rule 4).

Any change not listed here is refused with 409 INVALID_STATUS_TRANSITION.
Later stages add requests, allocations, collections and assignments here.
"""

from app.core.errors import AppError
from app.domain.enums import AssignmentStatus as A
from app.domain.enums import CollectionStatus as C
from app.domain.enums import ListingStatus as L
from app.domain.enums import RequestStatus as R

# Changes a PROVIDER may make to their own listing.
LISTING_PROVIDER_TRANSITIONS: dict[L, set[L]] = {
    L.ACTIVE: {L.PAUSED, L.CLOSED, L.REMOVED},
    L.PAUSED: {L.ACTIVE, L.CLOSED, L.REMOVED},
    L.FULLY_ALLOCATED: {L.CLOSED},
    L.CLOSED: set(),  # final
    L.EXPIRED: set(),  # final
    L.SUSPENDED: set(),  # only an administrator can lift a suspension
    L.REMOVED: set(),  # final
}

# Listings the provider may still edit (dates, quantity, description ...).
LISTING_EDITABLE = {L.ACTIVE, L.PAUSED}


def check_listing_transition(current: L, target: L) -> None:
    if target not in LISTING_PROVIDER_TRANSITIONS.get(current, set()):
        raise AppError(
            409, "INVALID_STATUS_TRANSITION",
            f"A listing that is '{current.value}' can't be changed to '{target.value}'.",
            {"current_status": current.value, "requested_status": target.value},
        )


# ------------------------------------------------------------------ food requests
REQUEST_TRANSITIONS: dict[R, set[R]] = {
    R.PENDING: {R.APPROVED, R.PARTIALLY_APPROVED, R.REJECTED, R.CANCELLED, R.EXPIRED},
    R.APPROVED: {R.CANCELLED, R.COMPLETED},  # cancel before handover; complete after receipt
    R.PARTIALLY_APPROVED: {R.CANCELLED, R.COMPLETED},
    R.REJECTED: set(),
    R.CANCELLED: set(),
    R.EXPIRED: set(),
    R.COMPLETED: set(),
}


def check_request_transition(current: R, target: R) -> None:
    if target not in REQUEST_TRANSITIONS.get(current, set()):
        raise AppError(
            409, "INVALID_STATUS_TRANSITION",
            f"A request that is '{current.value}' can't be changed to '{target.value}'.",
            {"current_status": current.value, "requested_status": target.value},
        )


# -------------------------------------------------------------------- collections
COLLECTION_TRANSITIONS: dict[C, set[C]] = {
    C.SCHEDULED: {C.HANDED_OVER, C.FAILED, C.CANCELLED},
    C.HANDED_OVER: {C.RECEIVED, C.FAILED},
    C.RECEIVED: set(),
    C.FAILED: set(),
    C.CANCELLED: set(),
}


def check_collection_transition(current: C, target: C) -> None:
    if target not in COLLECTION_TRANSITIONS.get(current, set()):
        raise AppError(
            409, "INVALID_STATUS_TRANSITION",
            f"A collection that is '{current.value}' can't be changed to '{target.value}'.",
            {"current_status": current.value, "requested_status": target.value},
        )


# --------------------------------------------------------- volunteer assignments
# The volunteer's own steps (spec 4.3.4): accepted -> on the way to pickup -> collected
# -> on the way to the receiver -> delivered; "failed" from any of these with a reason.
ASSIGNMENT_TRANSITIONS: dict[A, set[A]] = {
    A.OPEN: {A.ACCEPTED, A.ASSIGNED, A.CANCELLED},  # claimed by a volunteer, or assigned by an admin
    A.ASSIGNED: {A.ACCEPTED, A.DECLINED, A.CANCELLED},
    A.ACCEPTED: {A.EN_ROUTE_PICKUP, A.COLLECTED, A.FAILED, A.CANCELLED},
    A.EN_ROUTE_PICKUP: {A.COLLECTED, A.FAILED, A.CANCELLED},
    A.COLLECTED: {A.EN_ROUTE_DROPOFF, A.DELIVERED, A.FAILED},
    A.EN_ROUTE_DROPOFF: {A.DELIVERED, A.FAILED},
    A.DELIVERED: {A.COMPLETED, A.FAILED},
    A.DECLINED: set(),
    A.COMPLETED: set(),
    A.FAILED: set(),
    A.CANCELLED: set(),
}

# Steps a volunteer may report through POST /assignments/{id}/status.
VOLUNTEER_STEPS = {A.EN_ROUTE_PICKUP, A.COLLECTED, A.EN_ROUTE_DROPOFF, A.DELIVERED, A.FAILED}


def check_assignment_transition(current: A, target: A) -> None:
    if target not in ASSIGNMENT_TRANSITIONS.get(current, set()):
        raise AppError(
            409, "INVALID_STATUS_TRANSITION",
            f"A task that is '{current.value}' can't be changed to '{target.value}'.",
            {"current_status": current.value, "requested_status": target.value},
        )
