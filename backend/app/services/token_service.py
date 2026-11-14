"""Refresh-token lifecycle: issue, rotate (with reuse detection), revoke.

Raw tokens are 256-bit random strings (secrets.token_urlsafe(32)). Because
they're long and random, a fast SHA-256 hash is safe for storing them (unlike
passwords, which people choose and need the slow Argon2 hash).
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import AccountStatus, ClientPlatform
from app.models.refresh_token import RefreshToken
from app.models.user import User


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _invalid(code: str = "INVALID_REFRESH_TOKEN", message: str = "Please log in again.") -> AppError:
    return AppError(401, code, message)


def issue_refresh_token(
    db: Session,
    settings: Settings,
    *,
    user: User,
    platform: ClientPlatform,
    device_label: str | None = None,
    family_id: uuid.UUID | None = None,
) -> tuple[str, RefreshToken]:
    """Create and store a new refresh token. The caller commits."""
    raw = secrets.token_urlsafe(32)
    row = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw),
        family_id=family_id or uuid.uuid4(),  # a new login starts a new family
        platform=platform,
        device_label=device_label,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(row)
    db.flush()  # assigns row.id without committing
    return raw, row


def rotate_refresh_token(db: Session, settings: Settings, raw: str) -> tuple[User, str, RefreshToken]:
    """Exchange a valid refresh token for a new one. Returns (user, new_raw, new_row).

    SELECT ... FOR UPDATE locks the token row, so if two requests send the same
    token at the same moment, the second waits for the first to finish and then
    sees the token as already used.
    """
    now = datetime.now(UTC)
    token = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw)).with_for_update(of=RefreshToken)
    )
    if token is None:
        raise _invalid()

    if token.revoked_at is not None:
        # A used/revoked token came back: it was probably copied. End that whole
        # login session everywhere, including the newest token in the family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
        raise _invalid("REFRESH_TOKEN_REUSED", "For your security this session was ended. Please log in again.")

    if token.expires_at <= now:
        raise _invalid("REFRESH_TOKEN_EXPIRED", "Your session has expired. Please log in again.")

    user = db.get(User, token.user_id)
    if user is None or user.deleted_at is not None:
        raise _invalid()
    if user.account_status == AccountStatus.SUSPENDED:
        raise AppError(403, "ACCOUNT_SUSPENDED", "Your account has been suspended. Please contact FreshLink support.")
    if user.account_status == AccountStatus.DEACTIVATED:
        raise AppError(403, "ACCOUNT_DEACTIVATED", "Your account has been deactivated. Please contact FreshLink support.")

    new_raw, new_row = issue_refresh_token(
        db, settings, user=user, platform=token.platform,
        device_label=token.device_label, family_id=token.family_id,
    )
    token.revoked_at = now
    token.replaced_by_id = new_row.id
    db.commit()
    return user, new_raw, new_row


def revoke_refresh_token(db: Session, raw: str) -> None:
    """Log out one device. Unknown or already-revoked tokens are ignored (idempotent)."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(raw), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


def revoke_all_for_user(db: Session, user_id: uuid.UUID) -> None:
    """Revoke every session of a user. The caller commits."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
