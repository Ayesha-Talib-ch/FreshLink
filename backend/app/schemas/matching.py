"""Matching response bodies (Technical Plan section 4.6)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class ReceiverCard(BaseModel):
    """What a provider sees about a MATCHED receiver: no phone or address (plan 5.3)."""

    id: uuid.UUID
    receiver_name: str
    category_name: str
    distance_km: float
    has_own_transport: bool


class MatchOut(BaseModel):
    rank: int
    score: float  # 0 to 1
    components: dict[str, float]  # the six parts, each 0 to 1
    reasons: list[str]
    has_active_request: bool
    receiver: ReceiverCard


class ExcludedOut(BaseModel):
    reason: str
    receiver: ReceiverCard


class ListingMatchesOut(BaseModel):
    listing_id: uuid.UUID
    model_version: str  # "heuristic-v1": explainable scoring, not machine learning
    weights: dict[str, float]
    generated_at: datetime
    matches: list[MatchOut]
    excluded: list[ExcludedOut]


class RefreshOut(BaseModel):
    listing_id: uuid.UUID
    stored: int
    model_version: str
