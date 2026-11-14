"""Authentication endpoints, used identically by the website and the mobile app.

The ONLY platform difference (Technical Plan section 5.1) is how the refresh
token travels:
  * website ("web"):   httpOnly cookie, sent automatically by the browser and
                       unreadable by JavaScript (protects against XSS theft)
  * mobile ("mobile"): JSON body; the app stores it in expo-secure-store
Every rule (rotation, reuse detection, expiry, revocation) is shared.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.core.config import Settings
from app.core.errors import AppError, error_responses
from app.core.rate_limit import LoginThrottle
from app.core.security import create_access_token
from app.domain.enums import ClientPlatform, UserRole
from app.domain.validation import normalize_phone
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.user import UserOut
from app.services import auth_service, token_service

router = APIRouter(prefix="/auth", tags=["auth"])


# ------------------------------------------------------------------ helpers


def get_login_throttle(request: Request) -> LoginThrottle:
    return request.app.state.login_throttle


Throttle = Annotated[LoginThrottle, Depends(get_login_throttle)]


def _cookie_path(settings: Settings) -> str:
    # The browser sends the cookie ONLY to /api/v1/auth/..., never to other endpoints.
    return f"{settings.api_v1_prefix}/auth"


def _set_refresh_cookie(response: Response, settings: Settings, raw: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
        httponly=True,  # JavaScript can't read it
        secure=settings.refresh_cookie_secure,  # HTTPS only (localhost allowed by browsers)
        samesite="strict",  # not sent when another website triggers a request
        path=_cookie_path(settings),
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=_cookie_path(settings),
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _read_refresh_token(request: Request, settings: Settings, body: RefreshRequest | None) -> str | None:
    """Mobile: token in the body. Website: token in the cookie, plus an Origin check."""
    if body is not None and body.refresh_token:
        return body.refresh_token
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw is not None:
        # Extra CSRF protection: a cookie is only accepted from our own website,
        # or from the API's own pages (e.g. the Swagger docs at /docs).
        own_origin = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("origin") not in {*settings.cors_origins, own_origin}:
            raise AppError(403, "ORIGIN_NOT_ALLOWED", "This request did not come from the FreshLink website.")
    return raw


def _issue_tokens(
    db: DbSession,
    settings: Settings,
    response: Response,
    user: User,
    platform: ClientPlatform,
    device_label: str | None = None,
) -> TokenResponse:
    """Create an access token + a new refresh-token family, commit, and deliver them."""
    raw_refresh, _ = token_service.issue_refresh_token(
        db, settings, user=user, platform=platform, device_label=device_label
    )
    db.commit()
    return _token_response(settings, response, user, platform, raw_refresh)


def _token_response(
    settings: Settings, response: Response, user: User, platform: ClientPlatform, raw_refresh: str
) -> TokenResponse:
    access, expires_in = create_access_token(
        settings, user_id=user.id, role=user.role.value, token_version=user.token_version
    )
    if platform == ClientPlatform.WEB:
        _set_refresh_cookie(response, settings, raw_refresh)
        body_refresh = None
    else:
        body_refresh = raw_refresh
    return TokenResponse(
        access_token=access, expires_in=expires_in, refresh_token=body_refresh,
        user=UserOut.model_validate(user),
    )


def _identifier_key(identifier: str) -> str:
    identifier = identifier.strip().lower()
    if "@" not in identifier:
        try:
            identifier = normalize_phone(identifier)  # 0300-... and +92300... = same account
        except ValueError:
            pass
    return f"account:{identifier}"


# ---------------------------------------------------------------- endpoints


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED, responses=error_responses(409))
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


@router.post("/login", response_model=TokenResponse, responses=error_responses(401, 403, 429))
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    throttle: Throttle,
) -> TokenResponse:
    """Log in with email OR phone + password.

    Returns an access token (15 min) and a refresh token (14 days):
    in a cookie for platform="web", in the body for platform="mobile".
    """
    ip_key = f"ip:{request.client.host if request.client else 'unknown'}"
    account_key = _identifier_key(body.identifier)

    wait = throttle.retry_after(ip=ip_key, account=account_key)
    if wait:
        raise AppError(
            429, "TOO_MANY_LOGIN_ATTEMPTS",
            f"Too many failed login attempts. Please try again in {wait} seconds.",
            {"retry_after_seconds": wait}, headers={"Retry-After": str(wait)},
        )
    try:
        user = auth_service.authenticate(db, identifier=body.identifier, password=body.password)
    except AppError as exc:
        if exc.code == "INVALID_CREDENTIALS":
            throttle.record_failure(ip=ip_key, account=account_key)
        raise
    throttle.succeeded(account=account_key)
    return _issue_tokens(db, settings, response, user, ClientPlatform(body.platform), body.device_label)


@router.post("/refresh", response_model=TokenResponse, responses=error_responses(401, 403))
def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    body: RefreshRequest | None = None,
) -> TokenResponse:
    """Swap a refresh token for a new access token AND a new refresh token (rotation).

    Using an old (already swapped) refresh token again ends that whole session.
    """
    raw = _read_refresh_token(request, settings, body)
    if not raw:
        raise AppError(401, "INVALID_REFRESH_TOKEN", "Please log in again.")
    user, new_raw, new_row = token_service.rotate_refresh_token(db, settings, raw)
    return _token_response(settings, response, user, new_row.platform, new_raw)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses(403))
def logout(
    request: Request, db: DbSession, settings: AppSettings, body: LogoutRequest | None = None
) -> Response:
    """Log out THIS device. Works even if the access token already expired."""
    raw = _read_refresh_token(request, settings, body)
    if raw:
        token_service.revoke_refresh_token(db, raw)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(user: CurrentUser, db: DbSession, settings: AppSettings) -> Response:
    """Log out on EVERY device (e.g. after losing a phone)."""
    auth_service.end_all_sessions(db, user)
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response


@router.post("/change-password", response_model=TokenResponse, responses=error_responses(400))
def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> TokenResponse:
    """Change the password. Every other device is logged out; this one gets new tokens."""
    auth_service.change_password(
        db, user, current_password=body.current_password, new_password=body.new_password
    )
    return _issue_tokens(db, settings, response, user, ClientPlatform(body.platform))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """The logged-in user's account, role and statuses (both apps call this on start-up)."""
    return UserOut.model_validate(user)
