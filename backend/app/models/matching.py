"""Stored match recommendations (Technical Plan section 3.3).

Matches are calculated live for display. A SNAPSHOT is stored when a listing is published
or when matches are refreshed, together with what happened afterwards (outcome). These
rows are the training and evaluation data for the learned ranker (v2) in the AI stage.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import MatchOutcome
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class MatchRecommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "match_recommendations"
    __table_args__ = (UniqueConstraint("listing_id", "receiver_id", name="uq_match_recommendations_listing_receiver"),)

    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("food_listings.id", ondelete="CASCADE"), index=True)
    receiver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receiver_profiles.id", ondelete="CASCADE"), index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    rank: Mapped[int] = mapped_column(Integer)
    components: Mapped[dict] = mapped_column(JSONB)
    reasons: Mapped[list] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(40))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # notifications stage
    outcome: Mapped[MatchOutcome] = mapped_column(str_enum(MatchOutcome, "match_outcome"),
                                                  server_default=MatchOutcome.NONE.value)
