"""Food request endpoints (Technical Plan section 4.5), the same for website and app."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Client, CurrentUser, DbSession, require_roles, require_verified
from app.core.errors import error_responses
from app.domain.enums import RequestStatus, UserRole
from app.models.user import User
from app.schemas.common import Page
from app.schemas.requests import CancelIn, RequestHistoryOut, RequestIn, RequestOut, RequestUpdateIn
from app.schemas.verification import ReasonIn
from app.services import request_service as svc

router = APIRouter(tags=["requests"])


def require_verified_receiver(user: Annotated[User, Depends(require_roles(UserRole.RECEIVER))]) -> User:
    return require_verified(user)


VerifiedReceiver = Annotated[User, Depends(require_verified_receiver)]
ProviderOrAdmin = Annotated[User, Depends(require_roles(UserRole.PROVIDER, UserRole.ADMIN))]


@router.post("/requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(403, 404, 409))
def create_request(body: RequestIn, user: VerifiedReceiver, db: DbSession):  # noqa: ANN201
    """Request food from a listing (verified, eligible receivers). The quantity must not exceed
    what is available or your capacity for this food category."""
    return svc.create(db, user, body)


@router.get("/requests", response_model=Page[RequestOut])
def list_requests(
    user: CurrentUser,
    db: DbSession,
    status: RequestStatus | None = None,
    listing_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Receivers see their own requests; providers see requests on their listings;
    administrators see all. Newest first."""
    return svc.list_requests(db, user, status=status, listing_id=listing_id, page=page, page_size=page_size)


@router.get("/requests/{request_id}", response_model=RequestOut, responses=error_responses(404))
def get_request(request_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    return svc.get_involved(db, user, request_id)


@router.patch("/requests/{request_id}", response_model=RequestOut, responses=error_responses(404, 409))
def update_request(request_id: uuid.UUID, body: RequestUpdateIn, user: VerifiedReceiver, db: DbSession):  # noqa: ANN201
    """Change quantity, purpose or collection preference while the request is pending."""
    return svc.update(db, user, request_id, body)


@router.post("/requests/{request_id}/cancel", response_model=RequestOut, responses=error_responses(404, 409))
def cancel_request(request_id: uuid.UUID, user: VerifiedReceiver, db: DbSession,
                   body: CancelIn | None = None):  # noqa: ANN201
    return svc.cancel(db, user, request_id, body.reason if body else None)


@router.post("/requests/{request_id}/reject", response_model=RequestOut, responses=error_responses(404, 409))
def reject_request(request_id: uuid.UUID, body: ReasonIn, user: ProviderOrAdmin, db: DbSession,
                   client: Client):  # noqa: ANN201
    """Reject a pending request. The reason is shown to the receiver."""
    return svc.reject(db, user, request_id, body.reason, client)


@router.get("/requests/{request_id}/history", response_model=list[RequestHistoryOut], responses=error_responses(404))
def request_history(request_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    return svc.history(db, user, request_id)


@router.get("/listings/{listing_id}/requests", response_model=list[RequestOut], responses=error_responses(404))
def incoming_requests(listing_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession,
                      status: RequestStatus | None = None):  # noqa: ANN201
    """All requests for one of your listings, first come first, with each receiver's
    details and distance."""
    return svc.for_listing(db, user, listing_id, status)
