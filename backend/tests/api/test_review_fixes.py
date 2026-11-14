"""Tests added during the full API review (after Stage 2a).

Each test either locks in a bug fix or covers a code path that no test ran
before (found with the coverage report: pytest --cov=app --cov-report=term-missing).
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pwdlib.hashers.argon2 import Argon2Hasher
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import TokenError, decode_access_token
from app.domain.enums import AccountStatus, UserRole
from app.main import create_app
from app.models.category import ProviderCategory
from app.models.user import User
from app.schemas.profiles import UserUpdateIn
from app.services import auth_service, profile_service
from tests.conftest import DEFAULT_PASSWORD, TEST_JWT_SECRET, MakeUser, login_headers

WEBSITE = "http://localhost:5173"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def _crash_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/test/crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail")

    return app


# ------------------------------------------------- fix 1: message prefix


def test_validator_messages_have_no_technical_prefix(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register", json={
        "email": "a@example.com", "phone": "0300 1234567", "full_name": "Ali Raza",
        "password": "short", "role": "receiver"})
    assert response.json()["error"]["details"]["fields"][0]["message"] == (
        "Password must be at least 8 characters long.")


# ------------------------------------------ fix 2: 500 reaches the website


def test_crash_response_carries_cors_header(test_settings: Settings) -> None:
    """Without the CORS header the browser hides the response from the website."""
    with TestClient(_crash_app(test_settings)) as client:
        response = client.get("/test/crash", headers={"Origin": WEBSITE})
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == WEBSITE
    assert response.json() == {"error": {"code": "INTERNAL_ERROR",
                                         "message": "Something went wrong on our side.", "details": {}}}
    assert "secret internal detail" not in response.text


def test_database_down_gives_503_with_cors_on_normal_endpoints(test_settings: Settings) -> None:
    broken = test_settings.model_copy(update={
        "database_url": SecretStr("postgresql://u:p@127.0.0.1:1/none"), "db_connect_timeout": 2})
    with TestClient(create_app(broken)) as client:
        response = client.post(LOGIN, json={"identifier": "a@example.com", "password": "Whatever-123"},
                               headers={"Origin": WEBSITE})
    assert response.status_code == 503 and _code(response) == "DATABASE_UNAVAILABLE"
    assert response.headers.get("access-control-allow-origin") == WEBSITE
    assert "psycopg" not in response.text and "127.0.0.1" not in response.text


# ---------------------------------- fix 3: testing from the Swagger page


@pytest.fixture
def web_client(test_settings: Settings):  # noqa: ANN201
    with TestClient(create_app(test_settings.model_copy(update={"refresh_cookie_secure": False}))) as client:
        yield client


def test_swagger_page_can_refresh_and_logout(web_client: TestClient, make_user: MakeUser) -> None:
    """Swagger runs on the API's own address; same-origin requests are safe from CSRF."""
    user = make_user()
    web_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    assert web_client.post(REFRESH, headers={"Origin": "http://testserver"}).status_code == 200
    assert web_client.post(LOGOUT, headers={"Origin": "http://testserver"}).status_code == 204


def test_other_websites_are_still_blocked(web_client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    web_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    response = web_client.post(REFRESH, headers={"Origin": "http://testserver.evil.example"})
    assert response.status_code == 403 and _code(response) == "ORIGIN_NOT_ALLOWED"


# ------------------------------------------ previously untested code paths


def test_deactivation_takes_effect_on_existing_token(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user()
    headers = login_headers(client, user.email)
    db_session.execute(update(User).where(User.id == user.id).values(account_status=AccountStatus.DEACTIVATED))
    db_session.commit()
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 403 and _code(response) == "ACCOUNT_DEACTIVATED"


def test_refresh_for_deactivated_or_deleted_user(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    deactivated, deleted = make_user(), make_user()
    tokens = {}
    for user in (deactivated, deleted):
        body = {"identifier": user.email, "password": DEFAULT_PASSWORD, "platform": "mobile"}
        tokens[user.id] = client.post(LOGIN, json=body).json()["refresh_token"]
    db_session.execute(update(User).where(User.id == deactivated.id).values(account_status=AccountStatus.DEACTIVATED))
    db_session.execute(update(User).where(User.id == deleted.id).values(deleted_at=datetime.now(UTC)))
    db_session.commit()
    r1 = client.post(REFRESH, json={"refresh_token": tokens[deactivated.id]})
    assert r1.status_code == 403 and _code(r1) == "ACCOUNT_DEACTIVATED"
    r2 = client.post(REFRESH, json={"refresh_token": tokens[deleted.id]})
    assert r2.status_code == 401 and _code(r2) == "INVALID_REFRESH_TOKEN"


def test_service_refuses_admin_role_even_if_schema_is_bypassed(db_session: Session) -> None:
    with pytest.raises(AppError) as info:
        auth_service.register_user(db_session, email="x@example.com", phone="+923000000001",
                                   full_name="X Y", password=DEFAULT_PASSWORD, role=UserRole.ADMIN)
    assert info.value.code == "ADMIN_REGISTRATION_FORBIDDEN"


def test_old_password_hash_is_upgraded_on_login(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    """If hashing settings get stronger later, existing users are upgraded silently at login."""
    user = make_user()
    weak_hash = Argon2Hasher(time_cost=1, memory_cost=8 * 1024).hash(DEFAULT_PASSWORD)
    db_session.execute(update(User).where(User.id == user.id).values(password_hash=weak_hash))
    db_session.commit()
    assert client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD}).status_code == 200
    db_session.expire_all()
    new_hash = db_session.get(User, user.id).password_hash
    assert new_hash != weak_hash and new_hash.startswith("$argon2id$")


@pytest.mark.parametrize(
    "payload",
    [
        {"sub": "USER", "tv": 0, "type": "refresh"},  # wrong token type
        {"sub": "not-a-uuid", "tv": 0, "type": "access"},  # broken user id
    ],
)
def test_malformed_token_claims_rejected(test_settings: Settings, payload: dict) -> None:
    now = datetime.now(UTC)
    claims = {**payload, "sub": str(uuid.uuid4()) if payload["sub"] == "USER" else payload["sub"],
              "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(test_settings, token)


def _provider_body(db: Session) -> dict:
    category_id = db.scalar(select(ProviderCategory.id).where(ProviderCategory.code == "bakery"))
    return {"address": "Main Boulevard, Lahore", "latitude": 31.52, "longitude": 74.35,
            "business_name": "Test Bakers", "provider_category_id": str(category_id),
            "contact_person": "Ali Raza", "contact_phone": "0300 1234567"}


def test_at_most_three_opening_periods_per_day(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    headers = login_headers(client, make_user(role=UserRole.PROVIDER).email)
    periods = [{"open": f"{h:02d}:00", "close": f"{h:02d}:30"} for h in (8, 10, 12, 14)]
    response = client.put("/api/v1/providers/me", headers=headers,
                          json={**_provider_body(db_session), "opening_hours": {"tue": periods}})
    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["message"] == "At most 3 opening periods per day (tue)."


def test_simultaneous_profile_creation_gives_conflict_not_crash(
    client: TestClient, make_user: MakeUser, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two devices create the profile at the same instant: both think none exists yet.
    The UNIQUE(user_id) rule lets only one through; the other gets a clean 409."""
    headers = login_headers(client, make_user(role=UserRole.PROVIDER).email)
    body = _provider_body(db_session)
    assert client.put("/api/v1/providers/me", headers=headers, json=body).status_code == 200
    monkeypatch.setattr(profile_service, "_get_profile", lambda *args: None)  # simulate the race
    response = client.put("/api/v1/providers/me", headers=headers, json=body)
    assert response.status_code == 409 and _code(response) == "CONFLICT"


def test_simultaneous_phone_change_gives_conflict_not_crash(make_user: MakeUser, db_session: Session) -> None:
    make_user(phone="+923331112222")
    user = make_user()
    user.phone = "+923331112222"  # pretend the friendly pre-check already passed for this number
    with pytest.raises(AppError) as info:
        profile_service.update_account(db_session, user, UserUpdateIn(full_name="X Y", phone="03331112222"))
    assert info.value.status_code == 409 and info.value.code == "PHONE_ALREADY_REGISTERED"
