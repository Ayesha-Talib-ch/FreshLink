"""Problem reports ("report a problem", spec 4.2 / 4.3 / 4.4.8; plan 3.3 issue_reports).

Any involved user can report a problem about a listing, request, collection or delivery task.
Administrators review and resolve it; the reporter is told the outcome. entity_id points to a
row in the table named by entity_type (no foreign key, because it can point to four tables).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import IssueCategory, IssueEntityType, IssueStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class IssueReport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "issue_reports"
    __table_args__ = (
        # One unresolved report per person per item: pressing "send" twice doesn't create two.
        Index("uq_issue_reports_one_open_per_reporter", "reporter_id", "entity_type", "entity_id",
              unique=True, postgresql_where=text("status <> 'resolved'")),
        Index("ix_issue_reports_status_created", "status", "created_at"),
        CheckConstraint("status <> 'resolved' OR resolution_note IS NOT NULL", name="resolved_needs_note"),
    )

    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[IssueEntityType] = mapped_column(str_enum(IssueEntityType, "issue_entity_type"))
    entity_id: Mapped[uuid.UUID] = mapped_column()
    category: Mapped[IssueCategory] = mapped_column(str_enum(IssueCategory, "issue_category"))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[IssueStatus] = mapped_column(str_enum(IssueStatus, "issue_status"),
                                                server_default=IssueStatus.OPEN.value)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
