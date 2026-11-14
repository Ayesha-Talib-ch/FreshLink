"""Profile business rules for providers, receivers and volunteers.

PUT endpoints "upsert": the first call creates the profile, later calls update it.
That suits the registration wizard (register -> log in -> fill in profile) and the
"Edit profile" screen on both apps with a single endpoint.
"""

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory
from app.models.profiles import (
    ProviderProfile,
    ReceiverFoodPreference,
    ReceiverProfile,
    VolunteerAvailability,
    VolunteerProfile,
)
from app.models.user import User
from app.schemas.profiles import (
    AvailabilityIn,
    FoodPreferencesIn,
    ProviderProfileIn,
    ReceiverProfileIn,
    UserUpdateIn,
    VolunteerProfileIn,
)

Profile = TypeVar("Profile", ProviderProfile, ReceiverProfile, VolunteerProfile)
Category = TypeVar("Category", ProviderCategory, ReceiverCategory, FoodCategory)

# Receiver category codes with extra rules (codes are fixed; names may be renamed by admins).
SMALL_BUSINESS = "small_business"
INDIVIDUAL = "individual"


def _get_profile(db: Session, model: type[Profile], user: User) -> Profile | None:
    return db.scalar(select(model).where(model.user_id == user.id))


def get_profile_or_404(db: Session, model: type[Profile], user: User) -> Profile:
    profile = _get_profile(db, model, user)
    if profile is None:
        # The apps use this code to show "Complete your profile" instead of an error.
        raise AppError(404, "PROFILE_NOT_FOUND", "You haven't completed your profile yet.")
    return profile


def _active_category(db: Session, model: type[Category], category_id, field: str) -> Category:  # noqa: ANN001
    category = db.get(model, category_id)
    if category is None or not category.is_active:
        raise AppError(
            422, "CATEGORY_NOT_AVAILABLE", "Please choose one of the available categories.",
            {"field": field},
        )
    return category


def _save(db: Session, obj: object) -> None:
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as exc:
        # e.g. the same user's two devices creating the profile at the same moment:
        # the UNIQUE(user_id) constraint lets only one through.
        db.rollback()
        raise AppError(409, "CONFLICT", "Your profile was changed at the same time. Please try again.") from exc
    db.refresh(obj)


def _apply(profile: object, data: dict) -> None:
    for key, value in data.items():
        setattr(profile, key, value)


# --------------------------------------------------------------------- provider


def upsert_provider_profile(db: Session, user: User, body: ProviderProfileIn) -> ProviderProfile:
    _active_category(db, ProviderCategory, body.provider_category_id, "provider_category_id")
    data = body.model_dump(mode="json") | {"provider_category_id": body.provider_category_id}
    profile = _get_profile(db, ProviderProfile, user) or ProviderProfile(user_id=user.id)
    _apply(profile, data)
    _save(db, profile)
    return profile


# --------------------------------------------------------------------- receiver


def upsert_receiver_profile(db: Session, user: User, body: ReceiverProfileIn) -> ReceiverProfile:
    category = _active_category(db, ReceiverCategory, body.receiver_category_id, "receiver_category_id")
    data = body.model_dump()

    if category.code == SMALL_BUSINESS and not body.not_for_resale_declared:
        raise AppError(
            422, "NOT_FOR_RESALE_REQUIRED",
            "Small food businesses must confirm that donated food will not be resold.",
            {"field": "not_for_resale_declared"},
        )
    if category.code == INDIVIDUAL:
        if body.household_size is None:
            raise AppError(
                422, "HOUSEHOLD_SIZE_REQUIRED", "Please enter how many people are in your household.",
                {"field": "household_size"},
            )
    else:
        data["household_size"] = None  # only meaningful for individuals/households

    profile = _get_profile(db, ReceiverProfile, user) or ReceiverProfile(user_id=user.id)
    _apply(profile, data)
    _save(db, profile)
    return profile


def replace_food_preferences(db: Session, user: User, body: FoodPreferencesIn) -> ReceiverProfile:
    profile = _get_profile(db, ReceiverProfile, user)
    if profile is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your receiver profile first.")
    for pref in body.preferences:
        _active_category(db, FoodCategory, pref.food_category_id, "food_category_id")

    # Delete the old rows, then insert the new list, in ONE transaction: if anything
    # fails, the old list stays. (Written as two explicit steps for readability;
    # SQLAlchemy would also handle a one-step list replacement correctly.)
    profile.food_preferences.clear()
    db.flush()
    profile.food_preferences.extend(
        ReceiverFoodPreference(
            food_category_id=p.food_category_id, max_quantity_per_pickup=p.max_quantity_per_pickup, unit=p.unit
        )
        for p in body.preferences
    )
    _save(db, profile)
    db.expire(profile, ["food_preferences"])
    return profile


# -------------------------------------------------------------------- volunteer


def upsert_volunteer_profile(db: Session, user: User, body: VolunteerProfileIn) -> VolunteerProfile:
    profile = _get_profile(db, VolunteerProfile, user) or VolunteerProfile(user_id=user.id)
    _apply(profile, body.model_dump())
    _save(db, profile)
    return profile


def replace_availability(db: Session, user: User, body: AvailabilityIn) -> VolunteerProfile:
    profile = _get_profile(db, VolunteerProfile, user)
    if profile is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your volunteer profile first.")
    profile.availability.clear()
    db.flush()
    profile.availability.extend(VolunteerAvailability(**slot.model_dump()) for slot in body.slots)
    _save(db, profile)
    db.expire(profile, ["availability"])
    return profile


# ------------------------------------------------------------------ user account


def update_account(db: Session, user: User, body: UserUpdateIn) -> User:
    if body.phone != user.phone and db.scalar(select(User.id).where(User.phone == body.phone)):
        raise AppError(409, "PHONE_ALREADY_REGISTERED", "An account with this phone number already exists.")
    user.full_name = body.full_name
    user.phone = body.phone
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "PHONE_ALREADY_REGISTERED", "An account with this phone number already exists.") from exc
    db.refresh(user)
    return user
