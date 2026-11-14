"""Food listing business rules (spec 4.1 "Add / Manage Food Listings", 5.2).

Who may do what:
- create / edit / pause / resume / close / remove: the VERIFIED provider who owns it
- view: the owner, administrators, and verified receivers (only while it's active)
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import FilePurpose, ListingStatus, UserRole
from app.domain.state_machines import LISTING_EDITABLE, check_listing_transition
from app.models.category import FoodCategory
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import ProviderProfile
from app.models.user import User
from app.models.verification import StoredFile
from app.schemas.listings import ListingIn

# How far ahead a listing may be scheduled. Surplus food is short-lived; a date in
# 2099 is almost certainly a typo.
MAX_DAYS_AHEAD = 30
# Small allowance for phone clocks that are a few minutes fast.
CLOCK_TOLERANCE = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(UTC)


def _field_error(code: str, message: str, field: str) -> AppError:
    return AppError(422, code, message, {"field": field})


def provider_profile_or_409(db: Session, user: User) -> ProviderProfile:
    profile = db.scalar(select(ProviderProfile).where(ProviderProfile.user_id == user.id))
    if profile is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your business profile first.")
    return profile


def _validate(db: Session, provider: ProviderProfile, body: ListingIn, now: datetime) -> FoodCategory:
    """Rules that need the database or the current time."""
    category = db.get(FoodCategory, body.food_category_id)
    if category is None or not category.is_active:
        raise _field_error("CATEGORY_NOT_AVAILABLE", "Please choose one of the available food categories.",
                           "food_category_id")

    # Category-specific date rules (spec 9.6).
    if category.requires_preparation_time and body.preparation_at is None:
        raise _field_error("PREPARATION_TIME_REQUIRED",
                           f"'{category.name}' listings need the preparation date and time.", "preparation_at")
    if category.requires_expiry_date and body.expiry_at is None:
        raise _field_error("EXPIRY_DATE_REQUIRED", f"'{category.name}' listings need the expiry date.", "expiry_at")

    for field in ("preparation_at", "packaging_at"):
        value = getattr(body, field)
        if value is not None and value > now + CLOCK_TOLERANCE:
            raise _field_error("DATE_IN_FUTURE", "This date can't be in the future.", field)

    for field in ("expiry_at", "best_before_at"):
        value = getattr(body, field)
        if value is not None and value <= now:
            raise _field_error("DATE_ALREADY_PASSED", "This date has already passed.", field)

    if body.collection_deadline <= now:
        raise _field_error("DATE_ALREADY_PASSED", "The collection deadline has already passed.", "collection_deadline")
    if body.collection_deadline > now + timedelta(days=MAX_DAYS_AHEAD):
        raise _field_error("DATE_TOO_FAR", f"Listings can be scheduled at most {MAX_DAYS_AHEAD} days ahead.",
                           "collection_deadline")
    if body.expiry_at is not None and body.collection_deadline > body.expiry_at:
        raise _field_error("DEADLINE_AFTER_EXPIRY",
                           "The collection deadline can't be after the expiry date.", "collection_deadline")

    if body.image_file_id is not None:
        image = db.get(StoredFile, body.image_file_id)
        if image is None or image.owner_user_id != provider.user_id or image.purpose != FilePurpose.LISTING_IMAGE:
            raise _field_error("INVALID_FILE", "The food photo was not found. Please upload it again.", "image_file_id")
    return category


def _apply(listing: FoodListing, provider: ProviderProfile, body: ListingIn) -> None:
    data = body.model_dump(exclude={"pickup_address", "pickup_latitude", "pickup_longitude"})
    for key, value in data.items():
        setattr(listing, key, value)
    if body.pickup_address is None:  # use the business address from the provider profile
        listing.pickup_address = provider.address
        listing.pickup_latitude = provider.latitude
        listing.pickup_longitude = provider.longitude
    else:
        listing.pickup_address = body.pickup_address
        listing.pickup_latitude = body.pickup_latitude
        listing.pickup_longitude = body.pickup_longitude


def _history(db: Session, listing: FoodListing, from_status: ListingStatus | None, to_status: ListingStatus,
             user: User, reason: str | None = None) -> None:
    db.add(ListingStatusHistory(listing_id=listing.id, from_status=from_status.value if from_status else None,
                                to_status=to_status.value, changed_by=user.id, reason=reason))


def create(db: Session, user: User, body: ListingIn) -> FoodListing:
    provider = provider_profile_or_409(db, user)
    _validate(db, provider, body, _now())
    listing = FoodListing(provider_id=provider.id, status=ListingStatus.ACTIVE)
    _apply(listing, provider, body)
    db.add(listing)
    db.flush()  # assigns listing.id for the history row
    _history(db, listing, None, ListingStatus.ACTIVE, user, "created")
    db.commit()
    db.refresh(listing)
    return listing


def _owned_for_update(db: Session, user: User, listing_id: uuid.UUID) -> FoodListing:
    # FOR UPDATE locks the row until commit, so two edits of the same listing run one
    # after the other. of=FoodListing: lock only the listing row, not joined rows.
    listing = db.scalar(select(FoodListing).where(FoodListing.id == listing_id).with_for_update(of=FoodListing))
    if listing is None or listing.provider.user_id != user.id or listing.status == ListingStatus.REMOVED:
        raise AppError(404, "NOT_FOUND", "Listing not found.")  # 404 also for other providers' listings
    return listing


def update(db: Session, user: User, listing_id: uuid.UUID, expected_version: int, body: ListingIn) -> FoodListing:
    listing = _owned_for_update(db, user, listing_id)
    if listing.version != expected_version:
        raise AppError(409, "STALE_VERSION",
                       "This listing was changed on another device. Reload it and try again.",
                       {"current_version": listing.version})
    if listing.status not in LISTING_EDITABLE:
        raise AppError(409, "LISTING_NOT_EDITABLE", f"A '{listing.status.value}' listing can't be edited.",
                       {"status": listing.status.value})
    if Decimal(body.total_quantity) < listing.allocated_quantity:
        raise _field_error("QUANTITY_BELOW_ALLOCATED",
                           f"{listing.allocated_quantity} {listing.unit.value} are already promised to receivers; "
                           "the total can't be lower than that.", "total_quantity")
    _validate(db, listing.provider, body, _now())
    _apply(listing, listing.provider, body)
    listing.version += 1
    db.commit()
    db.refresh(listing)
    return listing


def change_status(db: Session, user: User, listing_id: uuid.UUID, target: ListingStatus,
                  reason: str | None) -> FoodListing:
    listing = _owned_for_update(db, user, listing_id)
    check_listing_transition(listing.status, target)
    if target == ListingStatus.ACTIVE and listing.collection_deadline <= _now():
        raise AppError(409, "DEADLINE_PASSED", "The collection deadline has passed; this listing can't be resumed.")
    previous = listing.status
    listing.status = target
    listing.version += 1
    _history(db, listing, previous, target, user, (reason or "").strip() or None)
    db.commit()
    db.refresh(listing)
    return listing


def my_listings(db: Session, user: User, status: ListingStatus | None, page: int, page_size: int) -> dict[str, Any]:
    provider = provider_profile_or_409(db, user)
    query = select(FoodListing).where(FoodListing.provider_id == provider.id)
    # Removed listings are hidden unless explicitly asked for.
    query = query.where(FoodListing.status == status) if status else query.where(FoodListing.status != ListingStatus.REMOVED)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(FoodListing.created_at.desc(), FoodListing.id)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_visible(db: Session, user: User, listing_id: uuid.UUID) -> FoodListing:
    listing = db.get(FoodListing, listing_id)
    visible = listing is not None and (
        user.role == UserRole.ADMIN
        or (listing.provider.user_id == user.id and listing.status != ListingStatus.REMOVED)
        or (user.role == UserRole.RECEIVER and user.is_verified and listing.status == ListingStatus.ACTIVE)
    )
    if not visible:
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    return listing


def history(db: Session, user: User, listing_id: uuid.UUID) -> list[ListingStatusHistory]:
    listing = db.get(FoodListing, listing_id)
    if listing is None or not (user.role == UserRole.ADMIN or listing.provider.user_id == user.id):
        raise AppError(404, "NOT_FOUND", "Listing not found.")
    return list(db.scalars(select(ListingStatusHistory).where(ListingStatusHistory.listing_id == listing_id)
                           .order_by(ListingStatusHistory.created_at, ListingStatusHistory.id)))
