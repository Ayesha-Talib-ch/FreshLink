"""Columns that almost every table shares."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, func, text
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    # Random UUIDs instead of 1, 2, 3 ... so nobody can guess other records' URLs.
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )


class TimestampMixin:
    # timestamptz = stored in UTC; displayed in Asia/Karachi by the apps.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def str_enum(enum_cls: type, name: str) -> Enum:
    """Store an enum as VARCHAR + CHECK (not a native PostgreSQL ENUM type),
    so adding a value later is a simple migration (Technical Plan section 3.1)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )
