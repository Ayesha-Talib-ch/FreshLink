"""Role and verification checks (authorization layer 1).

Test-only endpoints use each dependency, then every kind of user calls each
endpoint. The expected table below is the source of truth: if a check is ever
weakened, one of these cases fails.
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, SuperAdminUser, VerifiedUser, require_roles
from app.core.config import Settings
from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.main import create_app
from app.models.user import User
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
PENDING = VerificationStatus.PENDING


def _app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/t/provider")
    def provider_only(user: Annotated[User, Depends(require_roles(UserRole.PROVIDER))]) -> dict:
        return {"ok": True}

    @app.get("/t/provider-or-receiver")
    def provider_or_receiver(
        user: Annotated[User, Depends(require_roles(UserRole.PROVIDER, UserRole.RECEIVER))],
    ) -> dict:
        return {"ok": True}

    @app.get("/t/verified")
    def verified_only(user: VerifiedUser) -> dict:
        return {"ok": True}

    @app.get("/t/admin")
    def admin_only(user: AdminUser) -> dict:
        return {"ok": True}

    @app.get("/t/super-admin")
    def super_admin_only(user: SuperAdminUser) -> dict:
        return {"ok": True}

    return app


# (who, endpoint, expected status)
CASES = [
    ("anonymous", "/t/provider", 401),
    ("provider", "/t/provider", 200),
    ("receiver", "/t/provider", 403),
    ("volunteer", "/t/provider", 403),
    ("admin", "/t/provider", 403),
    ("provider", "/t/provider-or-receiver", 200),
    ("receiver", "/t/provider-or-receiver", 200),
    ("volunteer", "/t/provider-or-receiver", 403),
    ("anonymous", "/t/verified", 401),
    ("provider", "/t/verified", 200),
    ("unverified_provider", "/t/verified", 403),
    ("admin", "/t/verified", 200),  # admins need no verification
    ("anonymous", "/t/admin", 401),
    ("provider", "/t/admin", 403),
    ("volunteer", "/t/admin", 403),
    ("admin", "/t/admin", 200),
    ("super_admin", "/t/admin", 200),
    ("admin", "/t/super-admin", 403),
    ("super_admin", "/t/super-admin", 200),
    ("provider", "/t/super-admin", 403),
]


@pytest.fixture
def users(make_user: MakeUser) -> dict[str, User]:
    return {
        "provider": make_user(role=UserRole.PROVIDER, verification_status=APPROVED),
        "unverified_provider": make_user(role=UserRole.PROVIDER, verification_status=PENDING),
        "receiver": make_user(role=UserRole.RECEIVER, verification_status=APPROVED),
        "volunteer": make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED),
        "admin": make_user(role=UserRole.ADMIN, verification_status=APPROVED),
        "super_admin": make_user(role=UserRole.ADMIN, verification_status=APPROVED, is_super_admin=True),
    }


@pytest.mark.parametrize(("who", "path", "expected"), CASES)
def test_permission_matrix(
    test_settings: Settings, users: dict[str, User], who: str, path: str, expected: int
) -> None:
    with TestClient(_app(test_settings)) as client:
        headers = {} if who == "anonymous" else login_headers(client, users[who].email)
        response = client.get(path, headers=headers)
    assert response.status_code == expected, response.text


def test_verification_error_tells_the_app_the_current_status(
    test_settings: Settings, users: dict[str, User]
) -> None:
    with TestClient(_app(test_settings)) as client:
        headers = login_headers(client, users["unverified_provider"].email)
        error = client.get("/t/verified", headers=headers).json()["error"]
    assert error["code"] == "VERIFICATION_REQUIRED"
    assert error["details"] == {"verification_status": "pending"}


def test_suspended_admin_loses_admin_access(
    test_settings: Settings, make_user: MakeUser, db_session: Session
) -> None:
    admin = make_user(role=UserRole.ADMIN, verification_status=APPROVED)
    with TestClient(_app(test_settings)) as client:
        headers = login_headers(client, admin.email)
        db_session.execute(
            update(User).where(User.id == admin.id).values(account_status=AccountStatus.SUSPENDED)
        )
        db_session.commit()
        response = client.get("/t/admin", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
