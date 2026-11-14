"""Allocation runs (suggestions) and allocations (confirmed decisions)
(Technical Plan section 3.3, "Matching and allocation").

  allocation_runs       one suggestion: the policy used and a SNAPSHOT of every input,
                        so any recommendation can be reproduced and explained later
  allocation_run_lines  the suggested quantity + explanation for each request
  allocations           what the provider/admin CONFIRMED for each request

Spec 5.5: "The system must record: available quantity, requested quantity, recommended
allocation, approved allocation, actual collected quantity, remaining quantity."
  available   -> allocation_runs.available_snapshot
  requested   -> allocation_run_lines.requested_quantity
  recommended -> allocation_run_lines.suggested_quantity / allocations.suggested_quantity
  approved    -> allocations.approved_quantity
  collected   -> collections (collection stage)
  remaining   -> food_listings.total_quantity - allocated_quantity
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import AllocationRunPolicy, AllocationRunStatus, AllocationStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class AllocationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "allocation_runs"

    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_listings.id", ondelete="RESTRICT"), index=True)
    policy: Mapped[AllocationRunPolicy] = mapped_column(str_enum(AllocationRunPolicy, "allocation_policy"))
    solver: Mapped[str] = mapped_column(Text, server_default="greedy")
    status: Mapped[AllocationRunStatus] = mapped_column(
        str_enum(AllocationRunStatus, "allocation_run_status"), server_default=AllocationRunStatus.DRAFT.value
    )
    available_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # The listing version the suggestion was based on: if the listing changes before
    # confirmation, the suggestion is out of date ("superseded").
    listing_version: Mapped[int] = mapped_column(Integer)
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list["AllocationRunLine"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AllocationRunLine.position"
    )


class AllocationRunLine(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "allocation_run_lines"
    __table_args__ = (
        CheckConstraint("suggested_quantity >= 0 AND suggested_quantity <= requested_quantity",
                        name="suggested_within_requested"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("allocation_runs.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_requests.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer)  # display order (request order)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cap_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # min(requested, capacity), 0 if ineligible
    priority_weight: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    suggested_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    explanation: Mapped[str] = mapped_column(Text)

    request: Mapped["FoodRequest"] = relationship(lazy="joined", innerjoin=True)  # noqa: F821

    @property
    def receiver_name(self) -> str:
        return self.request.receiver.receiver_name

    @property
    def receiver_category(self) -> str:
        return self.request.receiver.category.name

    @property
    def requested_at(self) -> datetime:
        return self.request.created_at


class Allocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "allocations"
    __table_args__ = (
        CheckConstraint("approved_quantity > 0", name="approved_quantity_positive"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("allocation_runs.id", ondelete="RESTRICT"), index=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_listings.id", ondelete="RESTRICT"), index=True)
    # One allocation per request (unique): a request is decided once.
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_requests.id", ondelete="RESTRICT"), unique=True)
    receiver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receiver_profiles.id", ondelete="RESTRICT"), index=True)
    suggested_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    approved_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[AllocationStatus] = mapped_column(
        str_enum(AllocationStatus, "allocation_status"), server_default=AllocationStatus.CONFIRMED.value
    )
    decision_reason: Mapped[str] = mapped_column(Text)  # the line's explanation
    override_reason: Mapped[str | None] = mapped_column(Text)  # required when approved != suggested
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    run: Mapped[AllocationRun] = relationship(lazy="select")
    listing: Mapped["FoodListing"] = relationship(lazy="joined", innerjoin=True)  # noqa: F821
    receiver: Mapped["ReceiverProfile"] = relationship(lazy="joined", innerjoin=True)  # noqa: F821

    @property
    def receiver_name(self) -> str:
        return self.receiver.receiver_name
