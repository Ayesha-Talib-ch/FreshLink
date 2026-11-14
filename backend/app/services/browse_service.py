"""Browse / search / filter available food (spec 4.2 "Discover Available Food").

"Available" means ALL of these are true (checked in SQL, so it's fast):
  * status is active and there is quantity left (allocated < total)
  * the collection deadline, expiry date and best-before date haven't passed
  * for categories with a preparation limit (e.g. cooked meals), that limit hasn't passed

Distance is calculated in the database with the Haversine formula (app/engines/geo.py).
If the receiver gives no location, their profile's location and pickup radius are used.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import AccountStatus, ListingStatus, QuantityUnit, UrgencyLevel, UserRole
from app.engines.geo import haversine_km_sql
from app.models.category import UrgencyRuleRow
from app.models.listing import FoodListing
from app.models.profiles import ProviderProfile, ReceiverProfile
from app.models.user import User
from app.services import urgency_service

Sort = Literal["urgency", "distance", "deadline", "newest", "relevance"]

# Most urgent first. "incomplete" sits before "normal": missing information deserves a look.
_URGENCY_ORDER = {UrgencyLevel.CRITICAL: 0, UrgencyLevel.HIGH: 1, UrgencyLevel.MEDIUM: 2,
                  UrgencyLevel.INCOMPLETE: 3, UrgencyLevel.NORMAL: 4}
_SIMILARITY = 0.35  # how close a misspelling may be ("biryni" vs "biryani")


def active_provider_ids() -> Select:
    """Providers whose account is active (not suspended, deactivated or deleted)."""
    return select(ProviderProfile.id).join(User, User.id == ProviderProfile.user_id).where(
        User.account_status == AccountStatus.ACTIVE, User.deleted_at.is_(None))


def _available(query: Select, now: datetime) -> Select:
    preparation_limit_ok = or_(
        FoodListing.preparation_at.is_(None),
        UrgencyRuleRow.max_hours_since_preparation.is_(None),
        FoodListing.preparation_at
        + func.make_interval(0, 0, 0, 0, 0, 0, UrgencyRuleRow.max_hours_since_preparation * 3600) > now,
    )
    return query.outerjoin(UrgencyRuleRow, UrgencyRuleRow.food_category_id == FoodListing.food_category_id).where(
        FoodListing.status == ListingStatus.ACTIVE,
        FoodListing.allocated_quantity < FoodListing.total_quantity,
        FoodListing.collection_deadline > now,
        or_(FoodListing.expiry_at.is_(None), FoodListing.expiry_at > now),
        or_(FoodListing.best_before_at.is_(None), FoodListing.best_before_at > now),
        preparation_limit_ok,
        FoodListing.provider_id.in_(active_provider_ids()),  # suspended providers' food is hidden
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def browse(
    db: Session,
    user: User,
    *,
    q: str | None = None,
    food_category_id=None,  # noqa: ANN001
    unit: QuantityUnit | None = None,
    min_quantity: Decimal | None = None,
    near_lat: float | None = None,
    near_lng: float | None = None,
    radius_km: float | None = None,
    pickup_from: datetime | None = None,
    pickup_to: datetime | None = None,
    urgency: list[UrgencyLevel] | None = None,
    sort: Sort = "urgency",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    now = datetime.now(UTC)

    # ---- where to measure distance from
    if (near_lat is None) != (near_lng is None):
        raise AppError(422, "LOCATION_INCOMPLETE", "Send both near_lat and near_lng, or neither.")
    location_source = None
    if near_lat is not None:
        location_source = "request"
    elif user.role == UserRole.RECEIVER:
        profile = db.scalar(select(ReceiverProfile).where(ReceiverProfile.user_id == user.id))
        if profile is not None:
            near_lat, near_lng, location_source = profile.latitude, profile.longitude, "profile"
            if radius_km is None:
                radius_km = float(profile.max_pickup_radius_km)
    if radius_km is not None and location_source is None:
        raise AppError(422, "LOCATION_REQUIRED", "A distance filter needs a location (near_lat/near_lng).")
    if sort == "distance" and location_source is None:
        raise AppError(422, "LOCATION_REQUIRED", "Sorting by distance needs a location (near_lat/near_lng).")
    if min_quantity is not None and unit is None:
        # 10 kg and 10 packages are not comparable (Technical Plan issue H1).
        raise AppError(422, "UNIT_REQUIRED", "Choose a unit together with the minimum quantity.")
    if pickup_from is not None and pickup_to is not None and pickup_to < pickup_from:
        raise AppError(422, "INVALID_TIME_RANGE", "pickup_to must be after pickup_from.")

    distance = (haversine_km_sql(FoodListing.pickup_latitude, FoodListing.pickup_longitude, near_lat, near_lng)
                if location_source else None)
    query = _available(select(FoodListing), now)

    # ---- filters
    search = (q or "").strip()
    if search:
        query = query.where(or_(
            FoodListing.food_name.ilike(f"%{_escape_like(search)}%", escape="\\"),
            FoodListing.description.ilike(f"%{_escape_like(search)}%", escape="\\"),
            func.word_similarity(search, FoodListing.food_name) > _SIMILARITY,  # tolerates typos
        ))
    if food_category_id is not None:
        query = query.where(FoodListing.food_category_id == food_category_id)
    if unit is not None:
        query = query.where(FoodListing.unit == unit)
    if min_quantity is not None:
        query = query.where(FoodListing.total_quantity - FoodListing.allocated_quantity >= min_quantity)
    if pickup_from is not None:
        query = query.where(FoodListing.pickup_window_end >= pickup_from)
    if pickup_to is not None:
        query = query.where(FoodListing.pickup_window_start <= pickup_to)
    if urgency:
        query = query.where(FoodListing.urgency_level.in_(urgency))
    if distance is not None and radius_km is not None:
        query = query.where(distance <= radius_km)

    total = db.scalar(select(func.count()).select_from(query.with_only_columns(FoodListing.id).subquery()))

    # ---- sorting (FoodListing.id last, so pages never overlap or skip rows)
    urgency_rank = case({level.value: rank for level, rank in _URGENCY_ORDER.items()},
                        value=FoodListing.urgency_level, else_=9)
    order = {
        "urgency": [urgency_rank, FoodListing.collection_deadline],
        "distance": [distance],
        "deadline": [FoodListing.collection_deadline],
        "newest": [FoodListing.created_at.desc()],
        "relevance": [func.word_similarity(search, FoodListing.food_name).desc()] if search else [urgency_rank],
    }[sort]
    columns = [FoodListing] + ([distance.label("distance_km")] if distance is not None else [])
    rows = db.execute(query.with_only_columns(*columns).order_by(*order, FoodListing.id)
                      .offset((page - 1) * page_size).limit(page_size)).unique().all()

    items = []
    for row in rows:
        listing = row[0]
        listing.distance_km = round(row[1], 2) if distance is not None else None
        items.append(listing)
    urgency_service.attach(db, items, now)

    return {
        "items": items, "total": total, "page": page, "page_size": page_size,
        "location": ({"latitude": near_lat, "longitude": near_lng, "radius_km": radius_km, "source": location_source}
                     if location_source else None),
    }

