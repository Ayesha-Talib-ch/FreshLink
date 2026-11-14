"""Registration, login and /auth/me against the real test database."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import create_access_token
from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.models.user import User
from app.services import auth_service
from tests.conftest import DEFAULT_PASSWORD, MakeUser, login_headers

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


def _register_body(**overrides: str) -> dict[str, str]:
    body = {
        "email": "Ali.Bakery@Example.com",
        "phone": "0300 1234567",
        "full_name": "  Ali   Raza ",
        "password": DEFAULT_PASSWORD,
        "role": "provider",
    }
    body.update(overrides)
    return body


def _error_code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


# ---------------------------------------------------------------- register


@pytest.mark.parametrize("role", ["provider", "receiver", "volunteer"])
def test_register_each_public_role(client: TestClient, role: str) -> None:
    response = client.post(REGISTER, json=_register_body(role=role))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["role"] == role
    assert data["email"] == "ali.bakery@example.com"  # normalised
    assert data["phone"] == "+923001234567"  # normalised
    assert data["full_name"] == "Ali Raza"  # spaces cleaned
    assert data["verification_status"] == "not_submitted"
    assert data["is_verified"] is False
    assert data["account_status"] == "active"
    assert data["is_super_admin"] is False


def test_register_never_returns_or_stores_plain_password(client: TestClient, db_session: Session) -> None:
    response = client.post(REGISTER, json=_register_body())
    assert "password" not in response.text.lower()
    stored = db_session.scalar(select(User.password_hash))
    assert stored.startswith("$argon2id$") and DEFAULT_PASSWORD not in stored


def test_register_as_admin_is_rejected(client: TestClient, db_session: Session) -> None:
    response = client.post(REGISTER, json=_register_body(role="admin"))
    assert response.status_code == 422
    assert _error_code(response) == "VALIDATION_ERROR"
    assert db_session.scalar(select(User.id)) is None  # nothing was saved


def test_duplicate_email_is_case_insensitive(client: TestClient) -> None:
    assert client.post(REGISTER, json=_register_body()).status_code == 201
    response = client.post(REGISTER, json=_register_body(email="ALI.BAKERY@example.COM", phone="0311 7654321"))
    assert response.status_code == 409
    assert _error_code(response) == "EMAIL_ALREADY_REGISTERED"


def test_duplicate_phone_in_a_different_format(client: TestClient) -> None:
    assert client.post(REGISTER, json=_register_body()).status_code == 201
    response = client.post(REGISTER, json=_register_body(email="other@example.com", phone="+92-300-1234567"))
    assert response.status_code == 409
    assert _error_code(response) == "PHONE_ALREADY_REGISTERED"


@pytest.mark.parametrize(
    ("field", "value", "reported_as", "message_part"),
    [
        ("email", "not-an-email", "body.email", "email"),
        ("phone", "12345", "body.phone", "valid phone number"),
        ("full_name", " a ", "body.full_name", "full name"),
        # Password rules need the email too, so they're checked on the whole body.
        ("password", "short", "body", "at least 8"),
        ("password", "password123", "body", "too common"),
        ("role", "superuser", "body.role", "provider"),
    ],
)
def test_register_invalid_field_returns_422_with_clear_message(
    client: TestClient, field: str, value: str, reported_as: str, message_part: str
) -> None:
    response = client.post(REGISTER, json=_register_body(**{field: value}))
    assert response.status_code == 422
    fields = response.json()["error"]["details"]["fields"]
    assert len(fields) == 1, fields
    assert fields[0]["field"] == reported_as
    assert message_part in fields[0]["message"].lower()


def test_register_missing_fields(client: TestClient) -> None:
    response = client.post(REGISTER, json={})
    assert response.status_code == 422
    assert len(response.json()["error"]["details"]["fields"]) == 5


def test_simultaneous_duplicate_is_caught_by_database(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates two sign-ups at the same moment: the friendly pre-check is skipped,
    so only the database UNIQUE constraint can stop the duplicate."""
    monkeypatch.setattr(auth_service, "_ensure_unique", lambda *args: None)
    assert client.post(REGISTER, json=_register_body()).status_code == 201

    same_email = client.post(REGISTER, json=_register_body(phone="0311 7654321"))
    assert same_email.status_code == 409 and _error_code(same_email) == "EMAIL_ALREADY_REGISTERED"

    same_phone = client.post(REGISTER, json=_register_body(email="other@example.com"))
    assert same_phone.status_code == 409 and _error_code(same_phone) == "PHONE_ALREADY_REGISTERED"

    assert len(db_session.scalars(select(User)).all()) == 1


# ------------------------------------------------------------------- login


def test_login_with_email_any_case_and_use_token(client: TestClient, make_user: MakeUser) -> None:
    make_user(email="sara@example.com", role=UserRole.RECEIVER)
    response = client.post(LOGIN, json={"identifier": "SARA@Example.com", "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 15 * 60
    assert data["user"]["role"] == "receiver"

    me = client.get(ME, headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "sara@example.com"


def test_login_with_phone_in_local_format(client: TestClient, make_user: MakeUser) -> None:
    make_user(phone="+923331112222")
    response = client.post(LOGIN, json={"identifier": "0333-1112222", "password": DEFAULT_PASSWORD})
    assert response.status_code == 200


def test_login_records_last_login_time(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    user = make_user()
    client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    db_session.expire_all()
    assert db_session.get(User, user.id).last_login_at is not None


def test_wrong_password_and_unknown_email_give_the_same_answer(client: TestClient, make_user: MakeUser) -> None:
    user = make_user()
    wrong_password = client.post(LOGIN, json={"identifier": user.email, "password": "Wrong-Pass-99"})
    unknown_email = client.post(LOGIN, json={"identifier": "nobody@example.com", "password": "Wrong-Pass-99"})
    unknown_phone = client.post(LOGIN, json={"identifier": "not a phone", "password": "Wrong-Pass-99"})
    for response in (wrong_password, unknown_email, unknown_phone):
        assert response.status_code == 401
        assert response.json() == wrong_password.json()  # identical body: no hint which part was wrong
    assert _error_code(wrong_password) == "INVALID_CREDENTIALS"


@pytest.mark.parametrize(
    ("status", "code"),
    [(AccountStatus.SUSPENDED, "ACCOUNT_SUSPENDED"), (AccountStatus.DEACTIVATED, "ACCOUNT_DEACTIVATED")],
)
def test_blocked_accounts_cannot_log_in(client: TestClient, make_user: MakeUser, status: AccountStatus, code: str) -> None:
    user = make_user(account_status=status)
    response = client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 403
    assert _error_code(response) == code


def test_blocked_status_not_revealed_with_wrong_password(client: TestClient, make_user: MakeUser) -> None:
    user = make_user(account_status=AccountStatus.SUSPENDED)
    response = client.post(LOGIN, json={"identifier": user.email, "password": "Wrong-Pass-99"})
    assert response.status_code == 401 and _error_code(response) == "INVALID_CREDENTIALS"


def test_soft_deleted_user_cannot_log_in(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    user = make_user()
    user.deleted_at = datetime.now(UTC)
    db_session.commit()
    response = client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD})
    assert response.status_code == 401


def test_unverified_user_can_still_log_in(client: TestClient, make_user: MakeUser) -> None:
    user = make_user(verification_status=VerificationStatus.PENDING)
    assert client.post(LOGIN, json={"identifier": user.email, "password": DEFAULT_PASSWORD}).status_code == 200


# ---------------------------------------------------------------------- me


def test_me_without_token(client: TestClient) -> None:
    response = client.get(ME)
    assert response.status_code == 401
    assert _error_code(response) == "UNAUTHENTICATED"
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_with_garbage_token(client: TestClient) -> None:
    response = client.get(ME, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401 and _error_code(response) == "INVALID_TOKEN"


def test_me_with_expired_token(client: TestClient, make_user: MakeUser, test_settings: Settings) -> None:
    user = make_user()
    token, _ = create_access_token(
        test_settings, user_id=user.id, role=user.role.value, token_version=0,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    response = client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401 and _error_code(response) == "TOKEN_EXPIRED"


def test_bumping_token_version_revokes_existing_tokens(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user()
    headers = login_headers(client, user.email)
    assert client.get(ME, headers=headers).status_code == 200

    db_session.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    db_session.commit()
    response = client.get(ME, headers=headers)
    assert response.status_code == 401 and _error_code(response) == "TOKEN_REVOKED"


def test_suspension_takes_effect_on_existing_token(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    user = make_user()
    headers = login_headers(client, user.email)
    db_session.execute(update(User).where(User.id == user.id).values(account_status=AccountStatus.SUSPENDED))
    db_session.commit()
    response = client.get(ME, headers=headers)
    assert response.status_code == 403 and _error_code(response) == "ACCOUNT_SUSPENDED"


def test_deleted_user_token_rejected(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    user = make_user()
    headers = login_headers(client, user.email)
    db_session.execute(update(User).where(User.id == user.id).values(deleted_at=datetime.now(UTC)))
    db_session.commit()
    assert client.get(ME, headers=headers).status_code == 401
