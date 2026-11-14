"""Allowed status changes, in ONE place (Technical Plan section 2.2, rule 4).

Any change not listed here is refused with 409 INVALID_STATUS_TRANSITION.
Later stages add requests, allocations, collections and assignments here.
"""

from app.core.errors import AppError
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
