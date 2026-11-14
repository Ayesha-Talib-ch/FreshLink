"""In-app notifications, their categories, and admin announcements (spec section 13,
Technical Plan section 3.3).

A notification points at the thing it is about (entity_type + entity_id), so both apps
can open the right screen when it is tapped (a "deep link").
Push notifications to phones come with the mobile app (plan issue M1): push_status is
already here so the worker can send them later without a database change.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, false, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class NotificationCategory(Base):
    """Admin-managed: each category can be switched off (spec 4.4.9)."""

    __tablename__ = "notification_categories"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    default_push: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Notification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The bell icon asks "how many unread do I have?" every 30 s: this index makes it instant.
        Index("ix_notifications_user_unread_created", "user_id", "is_read", text("created_at DESC")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_code: Mapped[str] = mapped_column(ForeignKey("notification_categories.code", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(40))  # e.g. "request", "collection"
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    is_read: Mapped[bool] = mapped_column(Boolean, server_default=false())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    push_status: Mapped[str] = mapped_column(String(20), server_default="not_sent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Announcement(UUIDPrimaryKeyMixin, Base):
    """A message from administrators to everyone, one role, or selected users (spec 4.4.9).
    It is delivered as one notification per recipient."""

    __tablename__ = "announcements"

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    audience_type: Mapped[str] = mapped_column(String(10))  # all / role / users
    audience_role: Mapped[str | None] = mapped_column(String(20))
    recipient_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
