"""Matching endpoints (Technical Plan section 4.6).

Registered BEFORE the listings router, so /listings/recommended isn't mistaken for
/listings/{listing_id}.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_roles, require_verified
from app.core.errors import error_responses
from app.domain.enums import RequestStatus, UserRole
from app.models.user import User
from app.schemas.listings import ListingOut
from app.schemas.matching import ListingMatchesOut, RefreshOut
from app.schemas.requests import RequestOut
from app.services import matching_service
from app.services import request_service

router = APIRouter(tags=["matching"])

ProviderOrAdmin = Annotated[User, Depends(require_roles(UserRole.PROVIDER, UserRole.ADMIN))]


def _verified_receiver(user: Annotated[User, Depends(require_roles(UserRole.RECEIVER))]) -> User:
    return require_verified(user)


@router.get("/listings/recommended", response_model=list[ListingOut], responses=error_responses(409))
def recommended(user: Annotated[User, Depends(_verified_receiver)], db: DbSession,
                limit: Annotated[int, Query(ge=1, le=50)] = 20):  # noqa: ANN201
    """'Recommended for you': available food you qualify for, best match first, each with
    its match score and reasons. Food you already requested is left out."""
    return matching_service.recommended_for(db, user, limit)


@router.get("/listings/{listing_id}/matches", response_model=ListingMatchesOut, responses=error_responses(404))
def listing_matches(listing_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession) -> dict:
    """Suitable receivers for this listing, ranked, with score parts and reasons, plus the
    receivers that were excluded and why (spec 6.3)."""
    return matching_service.matches_for_listing(db, user, listing_id)


@router.post("/listings/{listing_id}/matches/refresh", response_model=RefreshOut, responses=error_responses(404))
def refresh_matches(listing_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession) -> dict:
    """Recalculate and store a snapshot of the matches (also done automatically on publish)."""
    return matching_service.refresh_for_user(db, user, listing_id)


@router.get("/listings/{listing_id}/requests", response_model=list[RequestOut], responses=error_responses(404))
def incoming_requests(listing_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession,
                      status: RequestStatus | None = None,
                      sort: Literal["first_come", "match"] = "first_come"):  # noqa: ANN201
    """All requests for one of your listings with each receiver's details, distance and
    MATCH score/reasons. sort=match puts the best-matching receivers first."""
    requests = request_service.for_listing(db, user, listing_id, status)
    if requests:
        scores = matching_service.scores_for_requests(db, requests[0].listing)
        for r in requests:
            r.match = scores.get(r.receiver_id)
        if sort == "match":
            def best_first(r):  # noqa: ANN001, ANN202
                score = r.match["score"] if r.match and r.match["score"] is not None else None
                # Scored receivers first (highest score first), excluded ones last; ties: first come.
                return (score is None, -(score or 0), r.created_at)

            requests.sort(key=best_first)
    return requests
