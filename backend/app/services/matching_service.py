"""Matching: connects the pure engine (app/engines/matching.py) to the database.

Three jobs (Technical Plan issue H6, so matching is useful and not decoration):
  1. for a provider/admin: ranked suitable receivers for a listing, with reasons, plus
     the receivers that were excluded and why            -> GET /listings/{id}/matches
  2. for a receiver: "Recommended for you"               -> GET /listings/recommended
  3. scores and reasons on a listing's incoming requests -> GET /listings/{id}/requests
The engine is v1 (explainable heuristic); stored snapshots feed the v2 ML ranker later.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    ACTIVE_REQUEST_STATUSES,
    AccountStatus,
    AllocationStatus,
    ListingStatus,
    MatchOutcome,
    UserRole,
    VerificationStatus,
)
from app.domain.formatting import format_quantity
from app.engines.geo import haversine_km, haversine_km_sql
from app.engines.matching import (
    MODEL_VERSION,
    ListingFacts,
    MatchResult,
    ReceiverFacts,
    rank,
)
from app.models.allocation import Allocation
from app.models.category import ReceiverCategory
from app.models.listing import FoodListing
from app.models.matching import MatchRecommendation
from app.models.profiles import ReceiverProfile
from app.models.request import FoodRequest
from app.models.user import User
from app.services import browse_service, urgency_service
from app.services import notification_service as notes
from app.services import settings_service

RECENT_DAYS = 30


def _max_weight(db: Session) -> Decimal:
    return db.scalar(select(func.max(ReceiverCategory.priority_weight)).where(ReceiverCategory.is_active.is_(True))) \
        or Decimal(1)


def _recent_allocation_counts(db: Session, receiver_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not receiver_ids:
        return {}
    since = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    rows = db.execute(select(Allocation.receiver_id, func.count()).where(
        Allocation.receiver_id.in_(receiver_ids), Allocation.created_at >= since,
        Allocation.status != AllocationStatus.CANCELLED).group_by(Allocation.receiver_id)).all()
    return dict(rows)


def _can_receive(profile: ReceiverProfile) -> bool:
    user = profile.user
    return (profile.is_eligible and user.deleted_at is None and user.account_status == AccountStatus.ACTIVE
            and user.verification_status == VerificationStatus.APPROVED)


def _facts(profile: ReceiverProfile, listing: FoodListing, distance: float, recent: int,
           allocated_ids: set[uuid.UUID]) -> ReceiverFacts:
    preference = next((p for p in profile.food_preferences if p.food_category_id == listing.food_category_id), None)
    return ReceiverFacts(
        receiver_id=profile.id, name=profile.receiver_name, category_name=profile.category.name,
        priority_weight=profile.category.priority_weight, distance_km=distance,
        pickup_radius_km=float(profile.max_pickup_radius_km), has_own_transport=profile.has_own_transport,
        can_receive=_can_receive(profile), accepts_category=preference is not None,
        capacity=preference.max_quantity_per_pickup if preference else None,
        capacity_unit_matches=preference is not None and preference.unit == listing.unit,
        recent_allocations=recent, already_allocated=profile.id in allocated_ids,
    )


def _listing_facts(db: Session, listing: FoodListing) -> ListingFacts:
    if getattr(listing, "urgency", None) is None:
        urgency_service.attach(db, [listing])
    return ListingFacts(food_category_name=listing.food_category.name, unit=listing.unit,
                        remaining=listing.remaining_quantity, urgency=listing.urgency.level)


def compute(db: Session, listing: FoodListing) -> tuple[MatchResult, dict[uuid.UUID, tuple[ReceiverProfile, float]]]:
    """Rank every receiver within the platform maximum distance (plus anyone who already
    requested this listing). Returns the engine result and each receiver's profile + distance."""
    distance = haversine_km_sql(ReceiverProfile.latitude, ReceiverProfile.longitude,
                                listing.pickup_latitude, listing.pickup_longitude)
    requested_here = select(FoodRequest.receiver_id).where(FoodRequest.listing_id == listing.id)
    max_travel = float(settings_service.get(db, "max_travel_km_with_transport"))
    profiles = db.execute(
        select(ReceiverProfile, distance).join(User, User.id == ReceiverProfile.user_id)
        .where(User.deleted_at.is_(None),
               or_(distance <= max_travel, ReceiverProfile.id.in_(requested_here)))
    ).unique().all()
    found = {p.id: (p, round(float(d), 2)) for p, d in profiles}

    allocated = set(db.scalars(select(Allocation.receiver_id).where(
        Allocation.listing_id == listing.id, Allocation.status != AllocationStatus.CANCELLED)))
    recent = _recent_allocation_counts(db, list(found))
    facts = [_facts(p, listing, d, recent.get(p.id, 0), allocated) for p, d in found.values()]
    return rank(_listing_facts(db, listing), facts, _max_weight(db),
                weights=settings_service.get(db, "matching_weights"), max_travel_km=max_travel), found


def _owned_listing(db: Session, user: User, listing_id: uuid.UUID) -> FoodListing:
    listing = db.get(FoodListing, listing_id)
    if listing is None or listing.status == ListingStatus.REMOVED or not (
        user.role == UserRole.ADMIN or listing.provider.user_id == user.id
    ):
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    return listing


def _receiver_card(profile: ReceiverProfile, distance: float) -> dict[str, Any]:
    # Names, category and distance only: phone/address appear only once they REQUEST (plan 5.3).
    return {"id": profile.id, "receiver_name": profile.receiver_name, "category_name": profile.category.name,
            "distance_km": distance, "has_own_transport": profile.has_own_transport}


def matches_for_listing(db: Session, user: User, listing_id: uuid.UUID) -> dict[str, Any]:
    listing = _owned_listing(db, user, listing_id)
    result, found = compute(db, listing)
    requested = set(db.scalars(select(FoodRequest.receiver_id).where(
        FoodRequest.listing_id == listing.id, FoodRequest.status.in_(ACTIVE_REQUEST_STATUSES))))
    return {
        "listing_id": listing.id, "model_version": result.model_version,
        "weights": settings_service.get(db, "matching_weights"),
        "generated_at": datetime.now(UTC),
        "matches": [{"rank": m.rank, "score": m.score, "components": m.components, "reasons": m.reasons,
                     "has_active_request": m.receiver_id in requested,
                     "receiver": _receiver_card(*found[m.receiver_id])} for m in result.matches],
        "excluded": [{"reason": e.reason, "receiver": _receiver_card(*found[e.receiver_id])}
                     for e in result.excluded],
    }


def notify_top_matches(db: Session, listing: FoodListing, k: int | None = None) -> int:
    """Spec 4.2.6 "new relevant food listings": tell the best-matching receivers (plan H6, job 1).
    k defaults to the "match_notify_top_k" setting. Each receiver is told about a listing only once
    (notified_at). The caller commits."""
    k = settings_service.get(db, "match_notify_top_k") if k is None else k
    if k <= 0:
        return 0
    rows = db.scalars(select(MatchRecommendation).where(
        MatchRecommendation.listing_id == listing.id, MatchRecommendation.notified_at.is_(None))
        .order_by(MatchRecommendation.rank).limit(k)).all()
    profiles = {p.id: p for p in db.scalars(select(ReceiverProfile).where(
        ReceiverProfile.id.in_([r.receiver_id for r in rows])))}
    now = datetime.now(UTC)
    for row in rows:
        reasons = " · ".join(row.reasons[:3])
        notes.notify(db, [profiles[row.receiver_id].user_id], "listing_match", "Food you may need",
                     f"{listing.food_name} ({format_quantity(listing.remaining_quantity, listing.unit)}): {reasons}",
                     "listing", listing.id)
        row.notified_at = now
    return len(rows)


def refresh(db: Session, listing: FoodListing) -> int:
    """Store a snapshot of the current matches (outcomes already recorded are kept).
    The caller commits."""
    result, _ = compute(db, listing)
    now = datetime.now(UTC)
    for m in result.matches:
        statement = insert(MatchRecommendation).values(
            listing_id=listing.id, receiver_id=m.receiver_id, score=m.score, rank=m.rank,
            components=m.components, reasons=m.reasons, model_version=result.model_version, generated_at=now)
        db.execute(statement.on_conflict_do_update(
            constraint="uq_match_recommendations_listing_receiver",
            set_={"score": statement.excluded.score, "rank": statement.excluded.rank,
                  "components": statement.excluded.components, "reasons": statement.excluded.reasons,
                  "model_version": statement.excluded.model_version, "generated_at": now}))
    return len(result.matches)


def refresh_for_user(db: Session, user: User, listing_id: uuid.UUID) -> dict[str, Any]:
    listing = _owned_listing(db, user, listing_id)
    stored = refresh(db, listing)
    db.commit()
    return {"listing_id": listing.id, "stored": stored, "model_version": MODEL_VERSION}


def record_outcome(db: Session, listing_id: uuid.UUID, receiver_id: uuid.UUID, outcome: MatchOutcome) -> None:
    """E.g. a recommended receiver requested the food. The caller commits."""
    recommendation = db.scalar(select(MatchRecommendation).where(
        MatchRecommendation.listing_id == listing_id, MatchRecommendation.receiver_id == receiver_id))
    if recommendation is not None:
        recommendation.outcome = outcome


def scores_for_requests(db: Session, listing: FoodListing) -> dict[uuid.UUID, dict[str, Any]]:
    """Match score/rank/reasons (or exclusion reason) per receiver, for the incoming-requests view."""
    result, _ = compute(db, listing)
    scores = {m.receiver_id: {"score": m.score, "rank": m.rank, "reasons": m.reasons, "excluded_reason": None}
              for m in result.matches}
    scores.update({e.receiver_id: {"score": None, "rank": None, "reasons": [], "excluded_reason": e.reason}
                   for e in result.excluded})
    return scores


def recommended_for(db: Session, user: User, limit: int) -> list[FoodListing]:
    """'Recommended for you': available listings this receiver qualifies for, best first.
    Listings they already requested are left out."""
    profile = db.scalar(select(ReceiverProfile).where(ReceiverProfile.user_id == user.id))
    if profile is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your receiver profile first.")
    max_travel = float(settings_service.get(db, "max_travel_km_with_transport"))
    radius = max_travel if profile.has_own_transport else float(profile.max_pickup_radius_km)
    page = browse_service.browse(db, user, radius_km=radius, sort="distance", page=1, page_size=100)
    already = set(db.scalars(select(FoodRequest.listing_id).where(
        FoodRequest.receiver_id == profile.id, FoodRequest.status.in_(ACTIVE_REQUEST_STATUSES))))

    max_weight = _max_weight(db)
    weights = settings_service.get(db, "matching_weights")
    picked: list[tuple[float, FoodListing]] = []
    for listing in page["items"]:
        if listing.id in already:
            continue
        distance = haversine_km(listing.pickup_latitude, listing.pickup_longitude, profile.latitude, profile.longitude)
        # Fairness compares receivers with each other; with one receiver it is neutral (0).
        facts = _facts(profile, listing, distance, recent=0, allocated_ids=set())
        result = rank(_listing_facts(db, listing), [facts], max_weight, weights=weights, max_travel_km=max_travel)
        if result.matches:
            match = result.matches[0]
            listing.match = {"score": match.score, "reasons": match.reasons, "model_version": MODEL_VERSION}
            picked.append((match.score, listing))
    picked.sort(key=lambda item: (-item[0], item[1].collection_deadline))
    return [listing for _, listing in picked[:limit]]
