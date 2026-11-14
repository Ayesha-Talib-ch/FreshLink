"""Password hashing and JWT access tokens (no database needed)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SECRET = "unit-test-secret-" + "y" * 40


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, jwt_secret=SecretStr(SECRET), access_token_expire_minutes=15)


def test_hash_is_argon2_salted_and_verifiable() -> None:
    first, second = hash_password("Correct-Horse-42"), hash_password("Correct-Horse-42")
    assert first.startswith("$argon2id$")
    assert "Correct-Horse-42" not in first
    assert first != second  # random salt each time
    assert verify_password("Correct-Horse-42", first)[0] is True
    assert verify_password("wrong-password", first)[0] is False


def test_token_round_trip(settings: Settings) -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(settings, user_id=user_id, role="provider", token_version=3)
    claims = decode_access_token(settings, token)
    assert (claims.user_id, claims.role, claims.token_version) == (user_id, "provider", 3)
    assert expires_in == 15 * 60


def test_expired_token_rejected(settings: Settings) -> None:
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    token, _ = create_access_token(settings, user_id=uuid.uuid4(), role="provider", token_version=0, now=long_ago)
    with pytest.raises(TokenError) as info:
        decode_access_token(settings, token)
    assert info.value.code == "TOKEN_EXPIRED"


def test_tampered_token_rejected(settings: Settings) -> None:
    token, _ = create_access_token(settings, user_id=uuid.uuid4(), role="provider", token_version=0)
    # Try to turn yourself into an admin by editing the payload: the signature no longer matches.
    header, payload, signature = token.split(".")
    forged_payload = jwt.utils.base64url_encode(
        jwt.utils.base64url_decode(payload).replace(b'"provider"', b'"admin"')
    ).decode()
    with pytest.raises(TokenError) as info:
        decode_access_token(settings, f"{header}.{forged_payload}.{signature}")
    assert info.value.code == "INVALID_TOKEN"


def test_token_signed_with_another_secret_rejected(settings: Settings) -> None:
    other = settings.model_copy(update={"jwt_secret": SecretStr("another-secret-" + "z" * 40)})
    token, _ = create_access_token(other, user_id=uuid.uuid4(), role="admin", token_version=0)
    with pytest.raises(TokenError):
        decode_access_token(settings, token)


def test_unsigned_alg_none_token_rejected(settings: Settings) -> None:
    payload = {"sub": str(uuid.uuid4()), "role": "admin", "tv": 0, "type": "access",
               "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=5)}
    unsigned = jwt.encode(payload, key=None, algorithm="none")
    with pytest.raises(TokenError):
        decode_access_token(settings, unsigned)


def test_token_missing_required_claims_rejected(settings: Settings) -> None:
    token = jwt.encode({"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(minutes=5)}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(settings, token)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x"])
def test_garbage_token_rejected(settings: Settings, garbage: str) -> None:
    with pytest.raises(TokenError):
        decode_access_token(settings, garbage)


def test_missing_or_short_secret_gives_clear_error() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET is not set"):
        Settings(_env_file=None).get_jwt_secret()
    with pytest.raises(ValueError, match="too short"):
        Settings(_env_file=None, jwt_secret=SecretStr("short")).get_jwt_secret()
