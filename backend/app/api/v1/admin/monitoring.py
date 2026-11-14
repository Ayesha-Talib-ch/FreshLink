"""Admin monitoring and moderation (spec 4.4.4-4.4.8; plan 4.12). Every change is audited.

Requests, allocations, collections and delivery tasks: use the shared list endpoints
(GET /requests, /allocations, /collections, /assignments), which return everything to admins."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import error_responses
from app.domain.enums import (
    AllocationRunPolicy,
    AllocationRunStatus,
    IssueCategory,
    IssueEntityType,
    IssueStatus,
    ListingStatus,
    UrgencyLevel,
    VerificationStatus,
)
from app.schemas.admin_monitoring import (
    AdminListingOut,
    AdminRunOut,
    AdminVolunteerOut,
    IssueOut,
    IssueUpdateIn,
    ModerationIn,
)
from app.schemas.common import Page
from app.services import admin_monitoring_service as svc
from app.services import issue_service

router = APIRouter(tags=["admin-monitoring"])
Pg = Annotated[int, Query(ge=1)]
PgSize = Annotated[int, Query(ge=1, le=100)]
Moderation = dict(response_model=AdminListingOut, responses=error_responses(404, 409))


# ---------------------------------------------------------------------- listings


@router.get("/listings", response_model=Page[AdminListingOut])
def all_listings(  # noqa: ANN201, PLR0913
    admin: AdminUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100, description="Food or provider name")] = None,
    status: ListingStatus | None = None,
    urgency: UrgencyLevel | None = None,
    food_category_id: uuid.UUID | None = None,
    provider_id: uuid.UUID | None = None,
    deadline_within_hours: Annotated[float | None, Query(gt=0, le=720)] = None,
    needs_attention: bool = False,
    sort: Literal["newest", "deadline"] = "newest",
    page: Pg = 1,
    page_size: PgSize = 20,
):
    """Every listing in any status, with date warnings (missing dates, deadline soon or passed).
    `needs_attention=true`: open listings that are critical, missing dates, or near their deadline."""
    return svc.list_listings(db, q=q, status=status, urgency=urgency, food_category_id=food_category_id,
                             provider_id=provider_id, deadline_within_hours=deadline_within_hours,
                             needs_attention=needs_attention, sort=sort, page=page, page_size=page_size)


@router.post("/listings/{listing_id}/suspend", **Moderation)
def suspend_listing(listing_id: uuid.UUID, body: ModerationIn, admin: AdminUser, db: DbSession,  # noqa: ANN201
                    client: Client):
    """Hide a listing from receivers until it is reinstated. Needs a reason; the provider is told."""
    return svc.moderate(db, admin, listing_id, "suspend", body.reason, client)


@router.post("/listings/{listing_id}/reinstate", **Moderation)
def reinstate_listing(listing_id: uuid.UUID, body: ModerationIn, admin: AdminUser, db: DbSession,  # noqa: ANN201
                      client: Client):
    """Lift a suspension (not after the collection deadline)."""
    return svc.moderate(db, admin, listing_id, "reinstate", body.reason, client)


@router.post("/listings/{listing_id}/close", **Moderation)
def close_listing(listing_id: uuid.UUID, body: ModerationIn, admin: AdminUser, db: DbSession,  # noqa: ANN201
                  client: Client):
    """End a listing; its pending requests expire. Food already allocated can still be collected."""
    return svc.moderate(db, admin, listing_id, "close", body.reason, client)


@router.post("/listings/{listing_id}/remove", **Moderation)
def remove_listing(listing_id: uuid.UUID, body: ModerationIn, admin: AdminUser, db: DbSession,  # noqa: ANN201
                   client: Client):
    """Remove an inappropriate listing (kept for history). Refused while food is still promised to receivers."""
    return svc.moderate(db, admin, listing_id, "remove", body.reason, client)


# -------------------------------------------------------------------- volunteers


@router.get("/volunteers", response_model=Page[AdminVolunteerOut])
def volunteers(  # noqa: ANN201
    admin: AdminUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100)] = None,
    available_now: bool | None = None,
    verification_status: VerificationStatus | None = None,
    page: Pg = 1,
    page_size: PgSize = 20,
):
    """Volunteers with availability, current load and completion rate."""
    return svc.list_volunteers(db, q=q, available_now=available_now, verification_status=verification_status,
                               page=page, page_size=page_size)


# --------------------------------------------------------------- allocation runs


@router.get("/allocation-runs", response_model=Page[AdminRunOut])
def allocation_runs(  # noqa: ANN201
    admin: AdminUser,
    db: DbSession,
    status: AllocationRunStatus | None = None,
    policy: AllocationRunPolicy | None = None,
    listing_id: uuid.UUID | None = None,
    overridden_only: bool = False,
    page: Pg = 1,
    page_size: PgSize = 20,
):
    """Suggested vs approved quantities for every allocation run, and how often people overrode them.
    Details of one run: GET /allocation-runs/{run_id}."""
    return svc.list_allocation_runs(db, status=status, policy=policy, listing_id=listing_id,
                                    overridden_only=overridden_only, page=page, page_size=page_size)


# ---------------------------------------------------------------- problem reports


@router.get("/issues", response_model=Page[IssueOut])
def all_issues(  # noqa: ANN201
    admin: AdminUser,
    db: DbSession,
    status: IssueStatus | None = None,
    unresolved_only: bool = False,
    entity_type: IssueEntityType | None = None,
    category: IssueCategory | None = None,
    page: Pg = 1,
    page_size: PgSize = 20,
):
    """All problem reports, newest first."""
    return issue_service.list_all(db, status=status, entity_type=entity_type, category=category,
                                  unresolved_only=unresolved_only, page=page, page_size=page_size)


@router.patch("/issues/{issue_id}", response_model=IssueOut, responses=error_responses(404, 409, 422))
def update_issue(issue_id: uuid.UUID, body: IssueUpdateIn, admin: AdminUser, db: DbSession,  # noqa: ANN201
                 client: Client):
    """Mark a report in review, or resolved (a note is required and sent to the reporter)."""
    return issue_service.update(db, admin, issue_id, body.status, body.resolution_note, client)
