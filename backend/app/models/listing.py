"""Food listings and their status history (Technical Plan section 3.3).

Quantities: total_quantity is what the provider offers; allocated_quantity is what
has been promised to receivers (filled by the allocation stage). The remaining
quantity is ALWAYS calculated (total - allocated), never stored, so the two
numbers can't drift apart (plan section 3.4).

All times are timestamptz (stored in UTC). Which date fields are required depends
on the food category (spec 9.6: "not every food category has identical rules").
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import DistributionType, ListingStatus, QuantityUnit, UrgencyLevel
from app.models.category import FoodCategory
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from app.models.profiles import ProviderProfile
from app.models.verification import StoredFile


class FoodListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "food_listings"
    __table_args__ = (
        CheckConstraint("total_quantity > 0", name="total_quantity_positive"),
        # The database itself makes over-allocation impossible (plan issue H5).
        CheckConstraint("allocated_quantity >= 0 AND allocated_quantity <= total_quantity",
                        name="allocated_within_total"),
        CheckConstraint("pickup_window_end > pickup_window_start", name="pickup_window_order"),
        CheckConstraint("collection_deadline >= pickup_window_start", name="deadline_after_window_start"),
        CheckConstraint("pickup_latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("pickup_longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("declaration_accepted", name="declaration_required"),
        Index("ix_food_listings_status_deadline", "status", "collection_deadline"),
        Index("ix_food_listings_status_urgency", "status", "urgency_level"),
        # Fuzzy search on the food name ("biryni" finds "Biryani"), used by receiver search.
        Index("ix_food_listings_food_name_trgm", "food_name",
              postgresql_using="gin", postgresql_ops={"food_name": "gin_trgm_ops"}),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_profiles.id", ondelete="RESTRICT"), index=True
    )
    food_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("food_categories.id", ondelete="RESTRICT"), index=True
    )
    food_name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)

    unit: Mapped[QuantityUnit] = mapped_column(str_enum(QuantityUnit, "listing_quantity_unit"))
    total_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    allocated_quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), server_default=text("0"))

    # Date facts supplied by the provider (which ones are required depends on the category).
    preparation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    packaging_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    best_before_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pickup_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pickup_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    collection_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    pickup_address: Mapped[str] = mapped_column(String(300))
    pickup_latitude: Mapped[float] = mapped_column(Double)
    pickup_longitude: Mapped[float] = mapped_column(Double)
    storage_info: Mapped[str | None] = mapped_column(Text)

    distribution_type: Mapped[DistributionType] = mapped_column(
        str_enum(DistributionType, "distribution_type"), server_default=DistributionType.FREE_DONATION.value
    )
    image_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    status: Mapped[ListingStatus] = mapped_column(
        str_enum(ListingStatus, "listing_status"), server_default=ListingStatus.ACTIVE.value
    )
    # Last urgency level stored by a write or by the urgency scan, so lists can be
    # filtered/sorted by urgency in SQL. The API always recalculates it live on read.
    urgency_level: Mapped[UrgencyLevel] = mapped_column(
        str_enum(UrgencyLevel, "urgency_level"), server_default=UrgencyLevel.NORMAL.value
    )
    urgency_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The provider's confirmation that the information is accurate (plan section 1.3).
    declaration_accepted: Mapped[bool] = mapped_column(Boolean)
    # Optimistic locking: every edit increases this number. An edit based on an
    # older version is refused (409), so two devices can't silently overwrite each other.
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    provider: Mapped[ProviderProfile] = relationship(lazy="joined", innerjoin=True)
    food_category: Mapped[FoodCategory] = relationship(lazy="joined", innerjoin=True)
    image: Mapped[StoredFile | None] = relationship(lazy="joined")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.total_quantity - self.allocated_quantity


class ListingStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Every status change of every listing (FR25). Never edited or deleted."""

    __tablename__ = "listing_status_history"

    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))  # None = created
    to_status: Mapped[str] = mapped_column(String(20))
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
