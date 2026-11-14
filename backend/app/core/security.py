"""Password hashing and access tokens.

PASSWORDS are hashed with Argon2id, a slow, memory-hard algorithm designed for
passwords. Even if the database leaked, the original passwords can't be read,
and guessing them is very expensive. Each hash includes a random "salt", so
two users with the same password get different hashes.

ACCESS TOKENS are JWTs (JSON Web Tokens): a small signed message saying "this
is user X with role Y, valid until time Z". The server signs it with
JWT_SECRET; if anyone changes a single character, the signature check fails.
The token is sent by the website and the mobile app in the header:
    Authorization: Bearer <token>
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import Settings

_password_hash = PasswordHash((Argon2Hasher(),))

# A real hash of a random value. Used when the login email doesn't exist, so
# the response takes the same time as a wrong password (prevents attackers
# from discovering which emails are registered by measuring response time).
_DUMMY_HASH = _password_hash.hash(uuid.uuid4().hex)


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Check a password. Returns (is_valid, new_hash_or_None).

    new_hash is set when the stored hash uses old settings and should be
    upgraded; the caller saves it.
    """
    return _password_hash.verify_and_update(password, password_hash)


def burn_dummy_verification(password: str) -> None:
    """Spend the same time as a real check (see _DUMMY_HASH)."""
    _password_hash.verify(password, _DUMMY_HASH)


ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    role: str
    token_version: int


class TokenError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def create_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    role: str,
    token_version: int,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Return (token, seconds_until_expiry)."""
    issued_at = now or datetime.now(UTC)
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),  # subject = which user
        "role": role,
        "tv": token_version,  # must match users.token_version, else revoked
        "type": ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": issued_at + lifetime,
    }
    token = jwt.encode(payload, settings.get_jwt_secret(), algorithm=settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.get_jwt_secret(),
            algorithms=[settings.jwt_algorithm],  # never accept "none" or other algorithms
            options={"require": ["sub", "exp", "iat", "tv", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("TOKEN_EXPIRED", "Your session has expired. Please log in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("INVALID_TOKEN", "Invalid authentication token.") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("INVALID_TOKEN", "Invalid authentication token.")
    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            role=str(payload.get("role", "")),
            token_version=int(payload["tv"]),
        )
    except (ValueError, TypeError) as exc:
        raise TokenError("INVALID_TOKEN", "Invalid authentication token.") from exc
