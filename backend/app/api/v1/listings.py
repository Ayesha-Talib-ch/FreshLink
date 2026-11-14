"""Food listing endpoints (Technical Plan section 4.4), same for website and app.

Editing uses OPTIMISTIC LOCKING: every listing has a version number. To edit, the
app sends the version it last saw in the If-Match header. If someone changed the
listing in between (e.g. the provider's phone and laptop), the edit is refused
with 409 STALE_VERSION instead of silently overwriting the other change.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import CurrentUser, DbSession, FoodBrowser, ProviderUser, VerifiedProvider
from app.core.errors import AppError, error_responses
from app.domain.enums import ListingStatus, QuantityUnit, UrgencyLevel
from app.models.listing import FoodListing
from app.schemas.common import Page
from app.schemas.listings import BrowsePage, ListingIn, ListingOut, StatusChangeIn, StatusHistoryOut, UrgencyOut
from app.services import browse_service
from app.services import listing_service as svc

router = APIRouter(tags=["listings"])


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise AppError(428, "VERSION_REQUIRED",
                       "Send the listing's current version in the If-Match header (reload the listing first).")
    cleaned = value.strip().removeprefix("W/").strip('"')
    if not cleaned.isdigit():
        raise AppError(422, "VALIDATION_ERROR", "If-Match must be the listing's version number, e.g. 3.",
                       {"fields": [{"field": "header.If-Match", "message": "Must be a whole number", "type": "int"}]})
    return int(cleaned)


def _with_etag(response: Response, listing: FoodListing) -> FoodListing:
    response.headers["ETag"] = f'"{listing.version}"'
    return listing


@router.post("/listings", response_model=ListingOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(409))
def create_listing(body: ListingIn, user: VerifiedProvider, db: DbSession, response: Response) -> FoodListing:
    """Add a food listing (verified providers only). Leave the pickup location empty to
    use your business address. The declaration must be accepted."""
    return _with_etag(response, svc.create(db, user, body))


@router.get("/listings", response_model=BrowsePage, responses=error_responses(422))
def browse_listings(
    user: FoodBrowser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100, description="Food name or description; small typos are fine")] = None,
    food_category_id: uuid.UUID | None = None,
    unit: QuantityUnit | None = None,
    min_quantity: Annotated[Decimal | None, Query(gt=0, description="Remaining quantity at least this (needs unit)")] = None,
    near_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    near_lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=100)] = None,
    pickup_from: Annotated[datetime | None, Query(description="With time zone, e.g. 2026-11-15T15:00:00+05:00")] = None,
    pickup_to: Annotated[datetime | None, Query(description="With time zone")] = None,
    urgency: Annotated[list[UrgencyLevel] | None, Query()] = None,
    sort: Literal["urgency", "distance", "deadline", "newest", "relevance"] = "urgency",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Available food for verified receivers: active, quantity left, no deadline passed.

    Without near_lat/near_lng, distances are measured from your saved receiver location,
    and only listings within your pickup radius are shown (pass radius_km to change it).
    Default order: most urgent first.
    """
    for name, value in (("pickup_from", pickup_from), ("pickup_to", pickup_to)):
        if value is not None and value.tzinfo is None:
            raise AppError(422, "VALIDATION_ERROR", f"{name} must include a time zone, e.g. +05:00.",
                           {"fields": [{"field": f"query.{name}", "message": "Time zone required", "type": "timezone"}]})
    return browse_service.browse(
        db, user, q=q, food_category_id=food_category_id, unit=unit, min_quantity=min_quantity,
        near_lat=near_lat, near_lng=near_lng, radius_km=radius_km, pickup_from=pickup_from, pickup_to=pickup_to,
        urgency=urgency, sort=sort, page=page, page_size=page_size,
    )


@router.get("/providers/me/listings", response_model=Page[ListingOut], responses=error_responses(409))
def my_listings(
    user: ProviderUser,
    db: DbSession,
    status: ListingStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Your listings, newest first. Removed listings appear only with status=removed."""
    return svc.my_listings(db, user, status, page, page_size)


@router.get("/listings/{listing_id}", response_model=ListingOut, responses=error_responses(404))
def get_listing(listing_id: uuid.UUID, user: CurrentUser, db: DbSession, response: Response) -> FoodListing:
    return _with_etag(response, svc.get_visible(db, user, listing_id))


@router.put("/listings/{listing_id}", response_model=ListingOut, responses=error_responses(404, 409, 428))
def update_listing(
    listing_id: uuid.UUID,
    body: ListingIn,
    user: VerifiedProvider,
    db: DbSession,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match", description="The listing version you are editing")] = None,
) -> FoodListing:
    """Edit your listing (send the whole listing). Only active or paused listings can be edited."""
    listing = svc.update(db, user, listing_id, _parse_if_match(if_match), body)
    return _with_etag(response, listing)


def _status_endpoint(target: ListingStatus, summary: str):  # noqa: ANN202
    def endpoint(listing_id: uuid.UUID, user: VerifiedProvider, db: DbSession, response: Response,
                 body: StatusChangeIn | None = None) -> FoodListing:
        return _with_etag(response, svc.change_status(db, user, listing_id, target, body.reason if body else None))

    endpoint.__doc__ = summary
    return endpoint


router.add_api_route("/listings/{listing_id}/pause", _status_endpoint(
    ListingStatus.PAUSED, "Hide the listing from receivers for now."), methods=["POST"], response_model=ListingOut,
    name="pause_listing", responses=error_responses(404, 409))
router.add_api_route("/listings/{listing_id}/resume", _status_endpoint(
    ListingStatus.ACTIVE, "Show a paused listing again."), methods=["POST"], response_model=ListingOut,
    name="resume_listing", responses=error_responses(404, 409))
router.add_api_route("/listings/{listing_id}/close", _status_endpoint(
    ListingStatus.CLOSED, "End the listing (it can't be reopened)."), methods=["POST"], response_model=ListingOut,
    name="close_listing", responses=error_responses(404, 409))


@router.delete("/listings/{listing_id}", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses(404, 409))
def remove_listing(listing_id: uuid.UUID, user: VerifiedProvider, db: DbSession) -> Response:
    """Remove the listing. It disappears from all lists but is kept in the database
    for history and reports (soft delete)."""
    svc.change_status(db, user, listing_id, ListingStatus.REMOVED, "removed by provider")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/listings/{listing_id}/history", response_model=list[StatusHistoryOut], responses=error_responses(404))
def listing_history(listing_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    """Every status change of the listing (owner and administrators)."""
    return svc.history(db, user, listing_id)


@router.get("/listings/{listing_id}/urgency", response_model=UrgencyOut, responses=error_responses(404))
def listing_urgency(listing_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    """Level, the deadline that drove it, hours left, and the rule applied (spec 6.2)."""
    return svc.get_visible(db, user, listing_id).urgency
