"""Audit log: a permanent record of important actions (FR24, spec 8.2).

"Append-only": rows can be added but never changed or deleted. A database
trigger (migration 0005) refuses UPDATE and DELETE, so even a bug in the
application can't rewrite history.

actor_id deliberately has NO foreign key: if a user account is deleted later,
their past actions must stay in the log (with the email snapshot).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_email: Mapped[str | None] = mapped_column(String(254))
    actor_role: Mapped[str | None] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(60), index=True)  # e.g. "verification.approve"
    entity_type: Mapped[str] = mapped_column(String(40))  # e.g. "user"
    entity_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
