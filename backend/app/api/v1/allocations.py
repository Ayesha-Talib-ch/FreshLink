"""Allocation endpoints (Technical Plan section 4.7), the same for website and app.

Typical flow for a provider with several requests on one listing:
  POST /listings/{id}/allocation-runs {"policy": "priority_weighted"}   -> suggestion (draft)
  POST /allocation-runs/{run_id}/confirm {"lines": []}                  -> accept as suggested
  (or change some lines, each with an override_reason)
For a single request: POST /requests/{id}/approve.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Client, CurrentUser, DbSession, require_roles
from app.core.errors import error_responses
from app.domain.enums import AllocationRunPolicy, AllocationStatus, UserRole
from app.models.user import User
from app.schemas.allocations import AllocationOut, AllocationRunIn, AllocationRunOut, ApproveIn, ConfirmIn
from app.schemas.common import Page
from app.schemas.verification import ReasonIn
from app.services import allocation_service as svc

router = APIRouter(tags=["allocation"])

ProviderOrAdmin = Annotated[User, Depends(require_roles(UserRole.PROVIDER, UserRole.ADMIN))]


@router.post("/listings/{listing_id}/allocation-runs", response_model=AllocationRunOut,
             status_code=status.HTTP_201_CREATED, responses=error_responses(404, 409))
def suggest_allocation(listing_id: uuid.UUID, body: AllocationRunIn, user: ProviderOrAdmin, db: DbSession):  # noqa: ANN201
    """Suggest how to divide the remaining food between the PENDING requests.
    Each line explains why. Nothing changes until you confirm."""
    return svc.suggest(db, user, listing_id, AllocationRunPolicy(body.policy))


@router.get("/listings/{listing_id}/allocation-runs", response_model=list[AllocationRunOut],
            responses=error_responses(404))
def listing_runs(listing_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession):  # noqa: ANN201
    """All suggestions for this listing, newest first (history of decisions)."""
    return svc.runs_for_listing(db, user, listing_id)


@router.get("/allocation-runs/{run_id}", response_model=AllocationRunOut, responses=error_responses(404))
def get_run(run_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession):  # noqa: ANN201
    return svc.get_run(db, user, run_id)


@router.post("/allocation-runs/{run_id}/confirm", response_model=AllocationRunOut,
             responses=error_responses(404, 409))
def confirm_run(run_id: uuid.UUID, body: ConfirmIn, user: ProviderOrAdmin, db: DbSession, client: Client):  # noqa: ANN201
    """Confirm the suggestion. To change a line, send its request_id, approved_quantity
    and an override_reason. Requests that get 0 are rejected with the explanation."""
    overrides = {line.request_id: (line.approved_quantity, line.override_reason) for line in body.lines}
    return svc.confirm(db, user, run_id, overrides, client)


@router.post("/allocation-runs/{run_id}/discard", response_model=AllocationRunOut,
             responses=error_responses(404, 409))
def discard_run(run_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession):  # noqa: ANN201
    return svc.discard(db, user, run_id)


@router.post("/requests/{request_id}/approve", response_model=AllocationOut, responses=error_responses(404, 409))
def approve_request(request_id: uuid.UUID, user: ProviderOrAdmin, db: DbSession, client: Client,
                    body: ApproveIn | None = None):  # noqa: ANN201
    """Approve one pending request (a one-line allocation). Leave the body empty to approve
    the suggested amount: the full request if the food and the receiver's capacity allow."""
    body = body or ApproveIn()
    return svc.approve_request(db, user, request_id, body.approved_quantity, body.override_reason, client)


@router.get("/allocations", response_model=Page[AllocationOut])
def list_allocations(
    user: CurrentUser,
    db: DbSession,
    listing_id: uuid.UUID | None = None,
    status: AllocationStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Receivers: food allocated to them. Providers: allocations on their listings. Admins: all."""
    return svc.list_allocations(db, user, listing_id=listing_id, status=status, page=page, page_size=page_size)


@router.get("/allocations/{allocation_id}", response_model=AllocationOut, responses=error_responses(404))
def get_allocation(allocation_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    return svc.get_allocation(db, user, allocation_id)


@router.post("/allocations/{allocation_id}/cancel", response_model=AllocationOut,
             responses=error_responses(404, 409))
def cancel_allocation(allocation_id: uuid.UUID, body: ReasonIn, user: ProviderOrAdmin, db: DbSession,
                      client: Client):  # noqa: ANN201
    """Undo a confirmed allocation (with a reason). The quantity becomes available again."""
    return svc.cancel_allocation(db, user, allocation_id, body.reason, client)
