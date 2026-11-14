"""Refresh tokens, logout, logout-all, change-password and login rate limiting."""

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import AccountStatus
from app.main import create_app
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import token_service
from tests.conftest import DEFAULT_PASSWORD, MakeUser

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
LOGOUT_ALL = "/api/v1/auth/logout-all"
CHANGE = "/api/v1/auth/change-password"
ME = "/api/v1/auth/me"
WEBSITE = "http://localhost:5173"
NEW_PASSWORD = "Brand-New-Pass-77"


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def _mobile_login(client: TestClient, user: User, password: str = DEFAULT_PASSWORD, label: str | None = None) -> dict:
    response = client.post(LOGIN, json={"identifier": user.email, "password": password,
                                        "platform": "mobile", "device_label": label})
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def web_client(test_settings: Settings) -> Iterator[TestClient]:
    """The test client talks plain http, so it would never send a Secure cookie back.
    For cookie round-trips we switch Secure off (the Secure flag itself is tested separately)."""
    settings = test_settings.model_copy(update={"refresh_cookie_secure": False})
    with TestClient(create_app(settings)) as client:
        yield client


# ---------------------------------------------------------------- login delivery


def test_mobile_login_returns_refresh_token_in_body_not_cookie(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    response = client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD, "platform": "mobile"})
    assert response.status_code == 200
    assert len(response.json()["refresh_token"]) >= 40
    assert "set-cookie" not in response.headers


def test_web_login_sets_secure_httponly_strict_cookie(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    response = client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
    assert response.json()["refresh_token"] is None  # never visible to website JavaScript
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("freshlink_refresh=")
    for attribute in ("httponly", "secure", "samesite=strict", "path=/api/v1/auth", f"max-age={14 * 24 * 3600}"):
        assert attribute in cookie, attribute


def test_refresh_token_stored_only_as_hash(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    tokens = _mobile_login(client, make_user(), label="Samsung A54")
    row = db_session.scalar(select(RefreshToken))
    assert row.token_hash == token_service.hash_refresh_token(tokens["refresh_token"])
    assert tokens["refresh_token"] not in row.token_hash
    assert row.platform == "mobile" and row.device_label == "Samsung A54"
    assert abs((row.expires_at - datetime.now(UTC)) - timedelta(days=14)) < timedelta(minutes=1)


# ---------------------------------------------------------------------- rotation


def test_refresh_rotates_both_tokens(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    first = _mobile_login(client, make_user())
    response = client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get(ME, headers=_bearer(second)).status_code == 200

    old, new = (db_session.scalar(select(RefreshToken).where(
        RefreshToken.token_hash == token_service.hash_refresh_token(t["refresh_token"])))
        for t in (first, second))
    assert old.revoked_at is not None and old.replaced_by_id == new.id
    assert new.revoked_at is None and new.family_id == old.family_id


def test_reusing_an_old_refresh_token_ends_the_whole_session(client: TestClient, make_user: MakeUser) -> None:
    first = _mobile_login(client, make_user())
    second = client.post(REFRESH, json={"refresh_token": first["refresh_token"]}).json()

    # An attacker replays the stolen first token ...
    replay = client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401 and _code(replay) == "REFRESH_TOKEN_REUSED"
    # ... so even the real user's newest token is now dead: the thief gains nothing.
    after = client.post(REFRESH, json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401


def test_reuse_does_not_affect_other_devices(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    phone = _mobile_login(client, user, label="phone")
    tablet = _mobile_login(client, user, label="tablet")
    client.post(REFRESH, json={"refresh_token": phone["refresh_token"]})
    client.post(REFRESH, json={"refresh_token": phone["refresh_token"]})  # reuse on the phone family
    assert client.post(REFRESH, json={"refresh_token": tablet["refresh_token"]}).status_code == 200


@pytest.mark.parametrize("body", [{"refresh_token": "made-up-token"}, {}, None])
def test_refresh_with_missing_or_unknown_token(client: TestClient, body: dict | None) -> None:
    response = client.post(REFRESH, json=body) if body is not None else client.post(REFRESH)
    assert response.status_code == 401 and _code(response) == "INVALID_REFRESH_TOKEN"


def test_expired_refresh_token(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    tokens = _mobile_login(client, make_user())
    db_session.execute(update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    db_session.commit()
    response = client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401 and _code(response) == "REFRESH_TOKEN_EXPIRED"


def test_suspended_user_cannot_refresh(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    user = make_user()
    tokens = _mobile_login(client, user)
    db_session.execute(update(User).where(User.id == user.id).values(account_status=AccountStatus.SUSPENDED))
    db_session.commit()
    response = client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 403 and _code(response) == "ACCOUNT_SUSPENDED"


def test_simultaneous_refresh_with_same_token_only_one_wins(
    test_db_engine, test_settings: Settings, client: TestClient, make_user: MakeUser,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests with the same token at the same instant (e.g. a token copied
    by an attacker). The row lock (SELECT ... FOR UPDATE) makes them run one after
    the other, so exactly one succeeds and the other is treated as reuse."""
    raw = _mobile_login(client, make_user())["refresh_token"]

    # Widen the gap between "read the token" and "save the new one" to 0.5 s, so
    # both requests are guaranteed to overlap. Without the row lock, both would
    # read the token as unused and BOTH would succeed; this test would then fail.
    original_issue = token_service.issue_refresh_token

    def slow_issue(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        time.sleep(0.5)
        return original_issue(*args, **kwargs)

    monkeypatch.setattr(token_service, "issue_refresh_token", slow_issue)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt() -> None:
        with Session(test_db_engine, expire_on_commit=False) as db:
            barrier.wait()
            try:
                token_service.rotate_refresh_token(db, test_settings, raw)
                results.append("ok")
            except AppError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert sorted(results) == ["REFRESH_TOKEN_REUSED", "ok"]
    with Session(test_db_engine) as db:
        assert db.scalar(select(RefreshToken.id).where(RefreshToken.revoked_at.is_(None))) is None


# ---------------------------------------------------------- website cookie flow


def test_web_refresh_uses_cookie_and_rotates_it(web_client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    web_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    old_cookie = web_client.cookies.get("freshlink_refresh")
    response = web_client.post(REFRESH, headers={"Origin": WEBSITE})
    assert response.status_code == 200, response.text
    assert response.json()["refresh_token"] is None
    assert web_client.cookies.get("freshlink_refresh") not in (None, old_cookie)


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example"}])
def test_web_refresh_cookie_rejected_from_other_origins(
    web_client: TestClient, make_user: MakeUser, headers: dict
) -> None:
    user = make_user()
    web_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    response = web_client.post(REFRESH, headers=headers)
    assert response.status_code == 403 and _code(response) == "ORIGIN_NOT_ALLOWED"


def test_web_logout_revokes_and_clears_cookie(web_client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    user = make_user()
    web_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    response = web_client.post(LOGOUT, headers={"Origin": WEBSITE})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith('freshlink_refresh=""') and "max-age=0" in cookie and "path=/api/v1/auth" in cookie
    assert web_client.cookies.get("freshlink_refresh") is None  # the browser forgets it
    assert db_session.scalar(select(RefreshToken.revoked_at)) is not None


# ------------------------------------------------------------------------ logout


def test_mobile_logout_revokes_only_that_device(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    phone, tablet = _mobile_login(client, user), _mobile_login(client, user)
    assert client.post(LOGOUT, json={"refresh_token": phone["refresh_token"]}).status_code == 204
    assert client.post(REFRESH, json={"refresh_token": phone["refresh_token"]}).status_code == 401
    assert client.post(REFRESH, json={"refresh_token": tablet["refresh_token"]}).status_code == 200


def test_logout_is_safe_to_repeat_and_needs_no_token(client: TestClient) -> None:
    assert client.post(LOGOUT).status_code == 204
    assert client.post(LOGOUT, json={"refresh_token": "unknown"}).status_code == 204


def test_logout_all_ends_every_session(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    phone, tablet = _mobile_login(client, user), _mobile_login(client, user)
    assert client.post(LOGOUT_ALL, headers=_bearer(phone)).status_code == 204
    for device in (phone, tablet):
        assert client.post(REFRESH, json={"refresh_token": device["refresh_token"]}).status_code == 401
        response = client.get(ME, headers=_bearer(device))  # access tokens die instantly too
        assert response.status_code == 401 and _code(response) == "TOKEN_REVOKED"


def test_logout_all_requires_login(client: TestClient) -> None:
    assert client.post(LOGOUT_ALL).status_code == 401


# --------------------------------------------------------------- change password


def test_change_password_success(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    this_phone, other_laptop = _mobile_login(client, user), _mobile_login(client, user)
    response = client.post(CHANGE, headers=_bearer(this_phone), json={
        "current_password": DEFAULT_PASSWORD, "new_password": NEW_PASSWORD, "platform": "mobile"})
    assert response.status_code == 200, response.text
    fresh = response.json()

    assert client.get(ME, headers=_bearer(fresh)).status_code == 200  # this device stays logged in
    assert client.get(ME, headers=_bearer(this_phone)).status_code == 401  # old access token dead
    assert client.post(REFRESH, json={"refresh_token": other_laptop["refresh_token"]}).status_code == 401
    assert client.post(REFRESH, json={"refresh_token": fresh["refresh_token"]}).status_code == 200

    old = client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    assert old.status_code == 401
    assert client.post(LOGIN, json={"identifier": user.email, "password": NEW_PASSWORD}).status_code == 200


@pytest.mark.parametrize(
    ("current", "new", "status", "code"),
    [
        ("Wrong-Current-1", NEW_PASSWORD, 400, "WRONG_CURRENT_PASSWORD"),
        (DEFAULT_PASSWORD, DEFAULT_PASSWORD, 400, "PASSWORD_UNCHANGED"),
        (DEFAULT_PASSWORD, "password123", 422, "VALIDATION_ERROR"),
        (DEFAULT_PASSWORD, "short", 422, "VALIDATION_ERROR"),
    ],
)
def test_change_password_rejections_keep_the_old_password(
    client: TestClient, make_user: MakeUser, current: str, new: str, status: int, code: str
) -> None:
    user = make_user()
    tokens = _mobile_login(client, user)
    response = client.post(CHANGE, headers=_bearer(tokens), json={"current_password": current, "new_password": new})
    assert response.status_code == status and _code(response) == code
    assert client.get(ME, headers=_bearer(tokens)).status_code == 200  # still logged in
    assert client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD}).status_code == 200


def test_change_password_requires_login(client: TestClient) -> None:
    response = client.post(CHANGE, json={"current_password": "x", "new_password": NEW_PASSWORD})
    assert response.status_code == 401


# ----------------------------------------------------------------- rate limiting


@pytest.fixture
def strict_client(test_settings: Settings) -> Iterator[TestClient]:
    settings = test_settings.model_copy(update={"login_max_failures_per_account": 3, "login_max_failures_per_ip": 3,
                                                "login_failure_window_seconds": 60})
    with TestClient(create_app(settings)) as client:
        yield client


def _wrong(client: TestClient, identifier: str):  # noqa: ANN202
    return client.post(LOGIN, json={"identifier": identifier, "password": "Wrong-Guess-00"})


def test_login_blocked_after_too_many_failures(strict_client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    for _ in range(3):
        assert _wrong(strict_client, user.email).status_code == 401
    # Even the CORRECT password is refused now: a guesser can't tell when they're right.
    blocked = strict_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    assert blocked.status_code == 429
    assert _code(blocked) == "TOO_MANY_LOGIN_ATTEMPTS"
    assert 1 <= int(blocked.headers["retry-after"]) <= 61
    assert blocked.json()["error"]["details"]["retry_after_seconds"] == int(blocked.headers["retry-after"])


def test_same_ip_guessing_many_accounts_is_blocked(strict_client: TestClient, make_user: MakeUser) -> None:
    victims = [make_user() for _ in range(3)]
    for victim in victims:
        _wrong(strict_client, victim.email)
    fresh_victim = make_user()
    assert _wrong(strict_client, fresh_victim.email).status_code == 429


def test_phone_formats_count_as_the_same_account(strict_client: TestClient, make_user: MakeUser) -> None:
    make_user(phone="+923001112233")
    for form in ("0300-1112233", "+92 300 1112233", "03001112233"):
        _wrong(strict_client, form)
    assert _wrong(strict_client, "923001112233").status_code == 429


def test_successful_login_resets_the_account_counter(test_settings: Settings, make_user: MakeUser) -> None:
    settings = test_settings.model_copy(update={"login_max_failures_per_account": 3})
    with TestClient(create_app(settings)) as client:
        user = make_user()
        _wrong(client, user.email)
        _wrong(client, user.email)
        assert client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD}).status_code == 200
        _wrong(client, user.email)
        _wrong(client, user.email)
        assert client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD}).status_code == 200


def test_suspended_or_wrong_status_errors_are_not_counted(strict_client: TestClient, make_user: MakeUser) -> None:
    user = make_user(account_status=AccountStatus.SUSPENDED)
    for _ in range(5):  # correct password, but suspended: 403, not a guessing attempt
        assert strict_client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD}).status_code == 403


def test_shared_ip_users_are_not_locked_out_by_each_other(client: TestClient, make_user: MakeUser) -> None:
    """Default limits: 6 different people on one IP each mistype once; the 7th can still log in."""
    for _ in range(6):
        assert _wrong(client, make_user().email).status_code == 401
    seventh = make_user()
    response = client.post(LOGIN, json={"identifier": seventh.email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 200
