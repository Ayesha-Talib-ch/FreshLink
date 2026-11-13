"""Reusable FastAPI dependencies.

A "dependency" is a function FastAPI runs before an endpoint and whose result
it passes into the endpoint (Depends(...)). Authorization is enforced HERE, on
the server. The website and app only hide buttons; they are never trusted.

Layer 1 of the three authorization layers (Technical Plan section 5.3):
    CurrentUser       - any logged-in, active user
    require_roles()   - only certain roles
    VerifiedUser      - account approved by an admin
    AdminUser / SuperAdminUser
Layers 2 (ownership) and 3 (field privacy) are added with the features that need them.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import TokenError, decode_access_token
from app.domain.enums import AccountStatus, UserRole
from app.models.user import User


def get_app_settings(request: Request) -> Settings:
    """Settings of the running app (set in create_app)."""
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Give the endpoint a database session and always close it afterwards.

    If the endpoint raises an error, everything it did in the database is
    rolled back, so a failed request never leaves half-saved data.
    Services call session.commit() themselves when their work succeeds.
    """
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]

# auto_error=False: we return our own error format instead of FastAPI's default.
_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")
_WWW_AUTH = {"WWW-Authenticate": "Bearer"}


def _unauthenticated(code: str, message: str) -> AppError:
    return AppError(401, code, message, headers=_WWW_AUTH)


def get_current_user(
    db: DbSession,
    settings: AppSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthenticated("UNAUTHENTICATED", "Please log in to continue.")
    try:
        claims = decode_access_token(settings, credentials.credentials)
    except TokenError as exc:
        raise _unauthenticated(exc.code, exc.message) from exc

    user = db.get(User, claims.user_id)
    if user is None or user.deleted_at is not None:
        raise _unauthenticated("INVALID_TOKEN", "Invalid authentication token.")
    # Checked on EVERY request, so suspending an account or changing a password
    # takes effect immediately, even for tokens that haven't expired yet.
    if claims.token_version != user.token_version:
        raise _unauthenticated("TOKEN_REVOKED", "Your session has ended. Please log in again.")
    if user.account_status == AccountStatus.SUSPENDED:
        raise AppError(403, "ACCOUNT_SUSPENDED", "Your account has been suspended. Please contact FreshLink support.")
    if user.account_status == AccountStatus.DEACTIVATED:
        raise AppError(403, "ACCOUNT_DEACTIVATED", "Your account has been deactivated. Please contact FreshLink support.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[User], User]:
    """Usage: user: Annotated[User, Depends(require_roles(UserRole.PROVIDER))]"""
    allowed = set(roles)

    def checker(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise AppError(403, "FORBIDDEN", "You don't have permission to do this.")
        return user

    return checker


def require_verified(user: CurrentUser) -> User:
    """Unverified users can log in, but may only use profile/verification/notifications."""
    if user.role != UserRole.ADMIN and not user.is_verified:
        raise AppError(
            403,
            "VERIFICATION_REQUIRED",
            "Your account must be approved by an administrator before you can do this.",
            {"verification_status": user.verification_status.value},
        )
    return user


def require_super_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN or not user.is_super_admin:
        raise AppError(403, "FORBIDDEN", "Only a super administrator can do this.")
    return user


VerifiedUser = Annotated[User, Depends(require_verified)]
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
SuperAdminUser = Annotated[User, Depends(require_super_admin)]
