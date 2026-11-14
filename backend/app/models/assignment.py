"""Volunteer assignments (Technical Plan section 3.3, "Collection and volunteers").

An assignment is one delivery task for one collection whose method is "volunteer".
It snapshots the pickup and drop-off points, so a later profile edit doesn't move a
delivery that is already on its way. volunteer_id is empty while the task is OPEN.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Double, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import AssignmentStatus
from app.models.collection import Collection
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.profiles import VolunteerProfile


class VolunteerAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "volunteer_assignments"
    __table_args__ = (
        # At most ONE active task per collection (plan 3.3), enforced by the database.
        Index("uq_volunteer_assignments_one_active_per_collection", "collection_id", unique=True,
              postgresql_where=text("status IN ('open', 'assigned', 'accepted', 'en_route_pickup', "
                                    "'collected', 'en_route_dropoff', 'delivered')")),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id", ondelete="RESTRICT"), index=True)
    volunteer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("volunteer_profiles.id", ondelete="RESTRICT"), index=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[AssignmentStatus] = mapped_column(
        str_enum(AssignmentStatus, "assignment_status"), server_default=AssignmentStatus.OPEN.value, index=True
    )
    pickup_address: Mapped[str] = mapped_column(String(300))
    pickup_latitude: Mapped[float] = mapped_column(Double)
    pickup_longitude: Mapped[float] = mapped_column(Double)
    dropoff_address: Mapped[str] = mapped_column(String(300))
    dropoff_latitude: Mapped[float] = mapped_column(Double)
    dropoff_longitude: Mapped[float] = mapped_column(Double)
    scheduled_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    collection: Mapped[Collection] = relationship(lazy="joined", innerjoin=True)
    volunteer: Mapped[VolunteerProfile | None] = relationship(lazy="joined")


class AssignmentStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Every status change of every task (FR25)."""

    __tablename__ = "assignment_status_history"

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("volunteer_assignments.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
