"""Connects the pure urgency engine (app/engines/urgency.py) to the database.

- assess_listing(): urgency of one listing, calculated LIVE (always up to date)
- attach(): puts the result on listings so API responses include it
- scan(): the periodic job. It expires listings whose deadline has passed and
  stores each listing's current level (used for SQL filtering/sorting later).
  It is IDEMPOTENT: running it twice in a row changes nothing the second time.
  The worker process (later stage) will run it every 5 minutes; until then:
      python -m app.cli urgency-scan
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import ListingStatus, UrgencyLevel
from app.engines.urgency import ListingDates, UrgencyResult, UrgencyRule, assess
from app.models.category import UrgencyRuleRow
from app.models.listing import FoodListing, ListingStatusHistory
from app.services import request_service
from app.services import notification_service as notes

OPEN_STATUSES = (ListingStatus.ACTIVE, ListingStatus.PAUSED)


def _to_rule(row: UrgencyRuleRow | None) -> UrgencyRule | None:
    if row is None:
        return None
    return UrgencyRule(
        medium_hours=float(row.medium_hours), high_hours=float(row.high_hours),
        critical_hours=float(row.critical_hours),
        max_hours_since_preparation=(float(row.max_hours_since_preparation)
                                     if row.max_hours_since_preparation is not None else None),
    )


def rules_by_category(db: Session, category_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, UrgencyRule | None]:
    ids = set(category_ids)
    rows = db.scalars(select(UrgencyRuleRow).where(UrgencyRuleRow.food_category_id.in_(ids))).all() if ids else []
    found = {r.food_category_id: _to_rule(r) for r in rows}
    return {i: found.get(i) for i in ids}


def _dates(listing: FoodListing) -> ListingDates:
    return ListingDates(
        collection_deadline=listing.collection_deadline, expiry_at=listing.expiry_at,
        best_before_at=listing.best_before_at, preparation_at=listing.preparation_at,
        requires_preparation_time=listing.food_category.requires_preparation_time,
        requires_expiry_date=listing.food_category.requires_expiry_date,
    )


def assess_listing(listing: FoodListing, rule: UrgencyRule | None, now: datetime | None = None) -> UrgencyResult:
    return assess(_dates(listing), rule, now or datetime.now(UTC))


def attach(db: Session, listings: list[FoodListing], now: datetime | None = None) -> list[FoodListing]:
    """Calculate urgency for each listing (one rules query for the whole list)."""
    now = now or datetime.now(UTC)
    rules = rules_by_category(db, (listing.food_category_id for listing in listings))
    for listing in listings:
        listing.urgency = assess_listing(listing, rules[listing.food_category_id], now)
    return listings


def store_level(listing: FoodListing, result: UrgencyResult, now: datetime) -> None:
    listing.urgency_level = result.level
    listing.urgency_computed_at = now


def scan(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    # SKIP LOCKED: a listing a provider is editing right now is simply checked next time,
    # instead of the job waiting for (or blocking) the provider.
    listings = db.scalars(
        select(FoodListing).where(FoodListing.status.in_(OPEN_STATUSES))
        .with_for_update(of=FoodListing, skip_locked=True)
    ).unique().all()
    rules = rules_by_category(db, (listing.food_category_id for listing in listings))

    expired: list[uuid.UUID] = []
    level_changes = 0
    expired_requests = 0
    for listing in listings:
        result = assess_listing(listing, rules[listing.food_category_id], now)
        if result.is_past_deadline:
            db.add(ListingStatusHistory(listing_id=listing.id, from_status=listing.status.value,
                                        to_status=ListingStatus.EXPIRED.value, changed_by=None,
                                        reason=result.explanation))
            listing.status = ListingStatus.EXPIRED
            listing.version += 1
            expired.append(listing.id)
            expired_requests += request_service.expire_pending_for_listing(
                db, listing, None, "the listing expired: " + result.explanation)
            notes.notify(db, [listing.provider.user_id], "listing_urgency", "Listing expired",
                         f"{listing.food_name}: {result.explanation}", "listing", listing.id)
        elif listing.urgency_level != result.level:
            level_changes += 1
            if result.level == UrgencyLevel.CRITICAL:  # notify once, when it BECOMES critical
                notes.notify(db, [listing.provider.user_id], "listing_urgency", "Listing needs attention",
                             f"{listing.food_name}: {result.explanation}", "listing", listing.id)
        store_level(listing, result, now)
    db.commit()
    return {"checked": len(listings), "expired": len(expired), "level_changes": level_changes,
            "expired_requests": expired_requests, "expired_ids": [str(i) for i in expired]}
