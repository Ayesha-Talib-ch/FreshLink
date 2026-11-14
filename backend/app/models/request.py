"""Food requests and their status history (Technical Plan section 3.3).

A request says "I'd like X of this listing". It does NOT reserve food: when several
receivers ask for more than is available, the allocation stage decides how much each
one gets (spec 5.5). The approved quantity therefore lives on the allocation, not
here (single source of truth, plan section 3.4).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import CollectionPreference, RequestStatus
from app.models.listing import FoodListing
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.profiles import ReceiverProfile


class FoodRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "food_requests"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="requested_quantity_positive"),
        # One ACTIVE request per receiver per listing, enforced by the database (spec 8.4:
        # "duplicate requests should be prevented"). Old cancelled/rejected ones don't count,
        # so a receiver can ask again after cancelling.
        Index("uq_food_requests_one_active_per_receiver", "listing_id", "receiver_id", unique=True,
              postgresql_where=text("status IN ('pending', 'approved', 'partially_approved')")),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_listings.id", ondelete="RESTRICT"), index=True)
    receiver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receiver_profiles.id", ondelete="RESTRICT"), index=True)
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    purpose: Mapped[str | None] = mapped_column(Text)  # intended use (optional)
    collection_preference: Mapped[CollectionPreference] = mapped_column(
        str_enum(CollectionPreference, "collection_preference"),
        server_default=CollectionPreference.SELF_PICKUP.value,
    )
    preferred_pickup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[RequestStatus] = mapped_column(
        str_enum(RequestStatus, "request_status"), server_default=RequestStatus.PENDING.value, index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    listing: Mapped[FoodListing] = relationship(lazy="joined", innerjoin=True)
    receiver: Mapped[ReceiverProfile] = relationship(lazy="joined", innerjoin=True)


class RequestStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Every status change of every request (FR25)."""

    __tablename__ = "request_status_history"

    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_requests.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
