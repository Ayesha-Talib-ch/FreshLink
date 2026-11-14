"""Collections: the physical handover of allocated food (Technical Plan section 3.3).

One collection per allocation. It works for direct pickup WITHOUT a volunteer (the spec's
draft schema required a volunteer assignment; plan issue M9 fixes that). Volunteer
assignments (next stage) attach to a collection when the method is "volunteer".

Quantity accounting (plan 3.5): spec 14.3 says listed, allocated and collected/delivered
quantities "must not be treated as interchangeable":
  allocated   = allocations.approved_quantity
  handed over = collections.handed_over_quantity  (what left the provider)
  received    = collections.received_quantity     (what actually arrived: "distributed")
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import CollectionMethod, CollectionStatus
from app.models.allocation import Allocation
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class Collection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("handed_over_quantity IS NULL OR handed_over_quantity > 0", name="handed_over_positive"),
        # You can't receive more than was handed over (plan 10.2 business test).
        CheckConstraint("received_quantity IS NULL OR (handed_over_quantity IS NOT NULL "
                        "AND received_quantity > 0 AND received_quantity <= handed_over_quantity)",
                        name="received_within_handed_over"),
    )

    allocation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("allocations.id", ondelete="RESTRICT"), unique=True
    )
    method: Mapped[CollectionMethod] = mapped_column(str_enum(CollectionMethod, "collection_method"))
    scheduled_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CollectionStatus] = mapped_column(
        str_enum(CollectionStatus, "collection_status"), server_default=CollectionStatus.SCHEDULED.value, index=True
    )
    handed_over_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handed_over_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    received_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # set when the worker sent the 'pickup soon' reminder, so it is sent only once (Stage 9)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    allocation: Mapped[Allocation] = relationship(lazy="joined", innerjoin=True)

    # Shortcuts used by the API responses.
    @property
    def listing(self):  # noqa: ANN201
        return self.allocation.listing

    @property
    def provider(self):  # noqa: ANN201
        return self.allocation.listing.provider

    @property
    def receiver(self):  # noqa: ANN201
        return self.allocation.receiver

    @property
    def approved_quantity(self) -> Decimal:
        return self.allocation.approved_quantity

    @property
    def request_id(self) -> uuid.UUID:
        return self.allocation.request_id
