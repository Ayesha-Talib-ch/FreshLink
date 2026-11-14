"""Receiver matching v1: explainable weighted scoring (spec 6.3, Technical Plan section 7.4).

Honest label: a HEURISTIC (hand-designed rules), not machine learning. A learned ranker
(v2) is planned for the AI stage, and will only replace this if it measurably does better.

Step 1 - hard filters. A receiver who fails any of these is EXCLUDED, with the reason:
  * account verified, active and eligible
  * accepts this food category (declared in their food preferences)
  * within their pickup radius, or (with their own transport) within the platform maximum
  * hasn't already been allocated food from this listing

Step 2 - score every remaining receiver from 0 to 1 as a weighted sum of six parts:
  distance            exp(-km / 5): 1 next door, ~0.37 at 5 km, ~0.14 at 10 km
  capacity fit        min(remaining, capacity) / max(remaining, capacity)
  collection fit      own transport 1.0, otherwise 0.5 (receivers' collection hours are
                      not collected yet, so transport is the honest proxy)
  urgency feasibility for high/critical urgency: 1 if close (<= 3 km) or own transport, else 0.2
  category priority   the category's priority weight / the highest weight
  fairness            1 - (allocations received in the last 30 days / most received by anyone)

Step 3 - reasons: plain-language phrases from the strongest parts.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.enums import QuantityUnit, UrgencyLevel
from app.domain.formatting import format_quantity

MODEL_VERSION = "heuristic-v1"
PLATFORM_MAX_RADIUS_KM = 50.0  # how far a receiver with own transport may travel
NEAR_KM = 3.0

# Default weights (plan section 7.4). They add up to 1, so the score is between 0 and 1.
WEIGHTS = {
    "distance": 0.30,
    "capacity_fit": 0.20,
    "collection_fit": 0.15,
    "urgency_feasibility": 0.15,
    "category_priority": 0.10,
    "fairness": 0.10,
}


@dataclass(frozen=True)
class ListingFacts:
    food_category_name: str
    unit: QuantityUnit
    remaining: Decimal
    urgency: UrgencyLevel


@dataclass(frozen=True)
class ReceiverFacts:
    receiver_id: Any
    name: str
    category_name: str
    priority_weight: Decimal
    distance_km: float
    pickup_radius_km: float
    has_own_transport: bool
    can_receive: bool  # verified, active, eligible
    accepts_category: bool
    capacity: Decimal | None  # max per pickup for this category, if declared
    capacity_unit_matches: bool
    recent_allocations: int  # in the last 30 days
    already_allocated: bool  # already has an allocation from THIS listing


@dataclass(frozen=True)
class Match:
    receiver_id: Any
    score: float
    rank: int
    components: dict[str, float]
    reasons: list[str]


@dataclass(frozen=True)
class Exclusion:
    receiver_id: Any
    reason: str


@dataclass(frozen=True)
class MatchResult:
    matches: list[Match] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    model_version: str = MODEL_VERSION


def _exclusion(r: ReceiverFacts, listing: ListingFacts, max_travel_km: float) -> str | None:
    if not r.can_receive:
        return "Account not verified, not active, or not eligible."
    if r.already_allocated:
        return "Already allocated food from this listing."
    if not r.accepts_category:
        return f"Doesn't accept {listing.food_category_name} (not in their food preferences)."
    if r.distance_km > r.pickup_radius_km and not (r.has_own_transport and r.distance_km <= max_travel_km):
        return (f"{r.distance_km:.1f} km away, outside their {r.pickup_radius_km:g} km pickup radius "
                "and no own transport.")
    return None


def _components(r: ReceiverFacts, listing: ListingFacts, max_weight: Decimal, most_recent: int) -> dict[str, float]:
    if r.capacity is not None and r.capacity_unit_matches and r.capacity > 0 and listing.remaining > 0:
        capacity_fit = float(min(listing.remaining, r.capacity) / max(listing.remaining, r.capacity))
    else:
        capacity_fit = 0.5  # unknown or not comparable (different unit): neutral
    urgent = listing.urgency in (UrgencyLevel.HIGH, UrgencyLevel.CRITICAL)
    feasible = r.distance_km <= NEAR_KM or r.has_own_transport
    return {
        "distance": math.exp(-r.distance_km / 5),
        "capacity_fit": capacity_fit,
        "collection_fit": 1.0 if r.has_own_transport else 0.5,
        "urgency_feasibility": (1.0 if feasible else 0.2) if urgent else 1.0,
        "category_priority": float(r.priority_weight / max_weight) if max_weight > 0 else 0.0,
        "fairness": 1.0 - (r.recent_allocations / most_recent) if most_recent > 0 else 1.0,
    }


def _reasons(r: ReceiverFacts, listing: ListingFacts, c: dict[str, float], most_recent: int) -> list[str]:
    reasons = [f"{r.distance_km:.1f} km away", f"accepts {listing.food_category_name}"]
    if r.capacity is not None and r.capacity_unit_matches:
        reasons.append(f"can take up to {format_quantity(r.capacity, listing.unit)}")
    if r.has_own_transport:
        reasons.append("has own transport")
    if listing.urgency in (UrgencyLevel.HIGH, UrgencyLevel.CRITICAL) and c["urgency_feasibility"] == 1.0:
        reasons.append("can collect urgent food quickly")
    if c["category_priority"] >= 0.99:
        reasons.append(f"priority category ({r.category_name})")
    if most_recent > 0 and c["fairness"] >= 0.5:
        reasons.append("received less food recently")
    return reasons


def rank(listing: ListingFacts, receivers: list[ReceiverFacts], max_weight: Decimal,
         weights: dict[str, float] | None = None, max_travel_km: float = PLATFORM_MAX_RADIUS_KM) -> MatchResult:
    """weights / max_travel_km: the admin's platform settings (defaults: WEIGHTS / PLATFORM_MAX_RADIUS_KM)."""
    weights = weights or WEIGHTS
    excluded, eligible = [], []
    for r in receivers:
        reason = _exclusion(r, listing, max_travel_km)
        (excluded.append(Exclusion(r.receiver_id, reason)) if reason else eligible.append(r))

    most_recent = max((r.recent_allocations for r in eligible), default=0)
    scored = []
    for r in eligible:
        c = _components(r, listing, max_weight, most_recent)
        score = sum(weights[name] * value for name, value in c.items())
        scored.append((r, round(score, 4), {k: round(v, 4) for k, v in c.items()}))
    # Highest score first; nearer receiver first on a tie (then a stable id order).
    scored.sort(key=lambda item: (-item[1], item[0].distance_km, str(item[0].receiver_id)))
    matches = [Match(r.receiver_id, score, i + 1, c, _reasons(r, listing, c, most_recent))
               for i, (r, score, c) in enumerate(scored)]
    return MatchResult(matches=matches, excluded=excluded)
