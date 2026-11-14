"""Refresh tokens: one row per logged-in device/session (Technical Plan section 3.3).

The raw token is given to the client ONCE and never stored. Only its SHA-256
hash is saved, so a database leak doesn't let anyone log in as the users.

Rotation: every time a refresh token is used it is revoked and replaced by a
new one in the same "family" (one family = one login on one device). If an
already-revoked token is presented again, someone has a copy of it, so the
whole family is revoked (reuse detection).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import ClientPlatform
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    platform: Mapped[ClientPlatform] = mapped_column(str_enum(ClientPlatform, "client_platform"))
    device_label: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
