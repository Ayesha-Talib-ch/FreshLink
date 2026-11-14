"""Food listing endpoints (Technical Plan section 4.4), same for website and app.

Editing uses OPTIMISTIC LOCKING: every listing has a version number. To edit, the
app sends the version it last saw in the If-Match header. If someone changed the
listing in between (e.g. the provider's phone and laptop), the edit is refused
with 409 STALE_VERSION instead of silently overwriting the other change.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.deps import CurrentUser, DbSession, ProviderUser, VerifiedProvider
from app.core.errors import AppError, error_responses
from app.domain.enums import ListingStatus
from app.models.listing import FoodListing
from app.schemas.common import Page
from app.schemas.listings import ListingIn, ListingOut, StatusChangeIn, StatusHistoryOut
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
