"""Authentication endpoints, used identically by the website and the mobile app.

Stage 1a: register, login, me.
Stage 1b adds: refresh, logout, logout-all, change-password.
"""

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.core.security import create_access_token
from app.domain.enums import UserRole
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: DbSession) -> UserOut:
    """Create a provider, receiver or volunteer account (status: not yet verified)."""
    user = auth_service.register_user(
        db,
        email=body.email,
        phone=body.phone,
        full_name=body.full_name,
        password=body.password,
        role=UserRole(body.role),
    )
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbSession, settings: AppSettings) -> TokenResponse:
    """Log in with email OR phone + password. Returns an access token."""
    user = auth_service.authenticate(db, identifier=body.identifier, password=body.password)
    token, expires_in = create_access_token(
        settings, user_id=user.id, role=user.role.value, token_version=user.token_version
    )
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """The logged-in user's account, role and statuses (both apps call this on start-up)."""
    return UserOut.model_validate(user)
