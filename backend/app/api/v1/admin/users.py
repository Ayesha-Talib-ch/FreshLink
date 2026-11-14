"""Admin user management (spec 4.4 "User Management"; plan 4.12). Changes are audited."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AdminUser, Client, DbSession, SuperAdminUser
from app.core.errors import error_responses
from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.schemas.admin_users import (
    AdminCreateIn,
    AdminUserDetail,
    AdminUserOut,
    HistoryItem,
    ReasonRequiredIn,
    StatusChangeIn,
)
from app.schemas.common import Page
from app.services import user_admin_service as svc

router = APIRouter(tags=["admin-users"])


@router.get("/users", response_model=Page[AdminUserOut])
def list_users(  # noqa: ANN201, PLR0913
    admin: AdminUser,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=100, description="Name, email, phone or organisation")] = None,
    role: UserRole | None = None,
    account_status: AccountStatus | None = None,
    verification_status: VerificationStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """View, search and filter all users (newest first)."""
    return svc.list_users(db, q=q, role=role, account_status=account_status,
                          verification_status=verification_status, page=page, page_size=page_size)


@router.get("/users/{user_id}", response_model=AdminUserDetail, responses=error_responses(404))
def get_user(user_id: uuid.UUID, admin: AdminUser, db: DbSession):  # noqa: ANN201
    """One user with their role profile and activity counts."""
    return svc.detail(db, user_id)


@router.get("/users/{user_id}/history", response_model=list[HistoryItem], responses=error_responses(404))
def user_history(user_id: uuid.UUID, admin: AdminUser, db: DbSession):  # noqa: ANN201
    """Account history: registration, verification and every audited action, newest first."""
    return svc.history(db, user_id)


@router.post("/users/{user_id}/activate", response_model=AdminUserDetail, responses=error_responses(403, 404, 409))
def activate(user_id: uuid.UUID, body: StatusChangeIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Re-open a suspended or deactivated account."""
    return svc.change_status(db, admin, user_id, "activate", body.reason, client)


@router.post("/users/{user_id}/deactivate", response_model=AdminUserDetail, responses=error_responses(403, 404, 409))
def deactivate(user_id: uuid.UUID, body: ReasonRequiredIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Close an account (e.g. the organisation stopped operating). Ends all its sessions."""
    return svc.change_status(db, admin, user_id, "deactivate", body.reason, client)


@router.post("/users/{user_id}/suspend", response_model=AdminUserDetail, responses=error_responses(403, 404, 409))
def suspend(user_id: uuid.UUID, body: ReasonRequiredIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Block an account when necessary (e.g. misuse). Ends all its sessions immediately."""
    return svc.change_status(db, admin, user_id, "suspend", body.reason, client)


@router.post("/admins", response_model=AdminUserDetail, status_code=status.HTTP_201_CREATED,
             responses=error_responses(409))
def create_admin(body: AdminCreateIn, super_admin: SuperAdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Super administrators only: create another administrator account."""
    return svc.create_admin(db, super_admin, body, client)
