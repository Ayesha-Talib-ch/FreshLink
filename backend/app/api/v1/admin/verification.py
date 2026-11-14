"""Admin: verification queue, decisions, receiver eligibility (spec 4.4, sections 2-4).

Every decision is written to the audit log in the same transaction.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import error_responses
from app.domain.enums import UserRole, VerificationRequestStatus
from app.schemas.common import Page
from app.schemas.profiles import ReceiverProfileOut
from app.schemas.verification import (
    EligibilityIn,
    ReasonIn,
    ReviewIn,
    VerificationAdminOut,
    VerificationQueueItem,
    VerificationRequestOut,
)
from app.services import verification_service as svc

router = APIRouter(tags=["admin-verification"])


@router.get("/verifications", response_model=Page[VerificationQueueItem])
def verification_queue(
    admin: AdminUser,
    db: DbSession,
    status: VerificationRequestStatus | None = VerificationRequestStatus.PENDING,
    role: UserRole | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Pending requests by default, oldest first. Filter by role ("Provider Verification",
    "Receiver Verification", "Volunteer Management" pages). Send status= empty for all."""
    return svc.queue(db, status=status, role=role, page=page, page_size=page_size)


@router.get("/verifications/{request_id}", response_model=VerificationAdminOut, responses=error_responses(404))
def verification_detail(request_id: uuid.UUID, admin: AdminUser, db: DbSession) -> dict:
    """The submission, the user's profile, required documents, and earlier submissions."""
    return svc.admin_detail(db, request_id)


@router.post("/verifications/{request_id}/approve", response_model=VerificationRequestOut,
             responses=error_responses(404, 409))
def approve(request_id: uuid.UUID, body: ReviewIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    return svc.decide(db, admin, request_id, "approve", (body.notes or "").strip() or None, client)


@router.post("/verifications/{request_id}/reject", response_model=VerificationRequestOut,
             responses=error_responses(404, 409))
def reject(request_id: uuid.UUID, body: ReasonIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """The reason is shown to the user, who can fix the problem and resubmit."""
    return svc.decide(db, admin, request_id, "reject", body.reason, client)


@router.post("/verifications/{request_id}/request-info", response_model=VerificationRequestOut,
             responses=error_responses(404, 409))
def request_info(request_id: uuid.UUID, body: ReasonIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Ask the user for more or clearer documents. They resubmit from the Verification screen."""
    return svc.decide(db, admin, request_id, "request_info", body.reason, client)


@router.patch("/receivers/{user_id}/eligibility", response_model=ReceiverProfileOut,
              responses=error_responses(404))
def set_eligibility(user_id: uuid.UUID, body: EligibilityIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Mark a receiver eligible / not eligible for food (a reason is required for 'not eligible')."""
    return svc.set_eligibility(db, admin, user_id, body.is_eligible, body.notes, client)
