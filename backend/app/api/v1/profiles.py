"""Profile endpoints (Technical Plan section 4.3).

Every endpoint works on "me", the logged-in user, so nobody can read or change
another user's profile by changing an id in the URL (prevents IDOR attacks).
Unverified users CAN use these: they need to complete their profile before an
administrator can approve them (plan section 5.2).
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, ProviderUser, require_roles
from app.core.errors import error_responses
from app.domain.enums import UserRole
from app.models.profiles import ProviderProfile, ReceiverProfile, VolunteerProfile
from app.models.user import User
from app.schemas.profiles import (
    AvailabilityIn,
    AvailabilitySlot,
    FoodPreferenceOut,
    FoodPreferencesIn,
    ProviderProfileIn,
    ProviderProfileOut,
    ReceiverProfileIn,
    ReceiverProfileOut,
    UserUpdateIn,
    VolunteerProfileIn,
    VolunteerProfileOut,
)
from app.schemas.user import UserOut
from app.services import profile_service as svc

router = APIRouter(tags=["profiles"])

ReceiverUser = Annotated[User, Depends(require_roles(UserRole.RECEIVER))]
VolunteerUser = Annotated[User, Depends(require_roles(UserRole.VOLUNTEER))]


# ------------------------------------------------------------------ any role


@router.get("/users/me", response_model=UserOut)
def get_account(user: CurrentUser) -> User:
    return user


@router.put("/users/me", response_model=UserOut, responses=error_responses(409))
def update_account(body: UserUpdateIn, user: CurrentUser, db: DbSession) -> User:
    return svc.update_account(db, user, body)


# ------------------------------------------------------------------ provider


@router.get("/providers/me", response_model=ProviderProfileOut, responses=error_responses(404))
def get_provider_profile(user: ProviderUser, db: DbSession) -> ProviderProfile:
    return svc.get_profile_or_404(db, ProviderProfile, user)


@router.put("/providers/me", response_model=ProviderProfileOut, responses=error_responses(409))
def save_provider_profile(body: ProviderProfileIn, user: ProviderUser, db: DbSession) -> ProviderProfile:
    """Create (first time) or update the business profile."""
    return svc.upsert_provider_profile(db, user, body)


# ------------------------------------------------------------------ receiver


@router.get("/receivers/me", response_model=ReceiverProfileOut, responses=error_responses(404))
def get_receiver_profile(user: ReceiverUser, db: DbSession) -> ReceiverProfile:
    return svc.get_profile_or_404(db, ReceiverProfile, user)


@router.put("/receivers/me", response_model=ReceiverProfileOut, responses=error_responses(409))
def save_receiver_profile(body: ReceiverProfileIn, user: ReceiverUser, db: DbSession) -> ReceiverProfile:
    return svc.upsert_receiver_profile(db, user, body)


@router.get("/receivers/me/food-preferences", response_model=list[FoodPreferenceOut], responses=error_responses(404))
def get_food_preferences(user: ReceiverUser, db: DbSession):  # noqa: ANN201
    return svc.get_profile_or_404(db, ReceiverProfile, user).food_preferences


@router.put("/receivers/me/food-preferences", response_model=list[FoodPreferenceOut], responses=error_responses(409))
def save_food_preferences(body: FoodPreferencesIn, user: ReceiverUser, db: DbSession):  # noqa: ANN201
    """Which food categories you accept and the most you can take per pickup (your capacity)."""
    return svc.replace_food_preferences(db, user, body).food_preferences


# ----------------------------------------------------------------- volunteer


@router.get("/volunteers/me", response_model=VolunteerProfileOut, responses=error_responses(404))
def get_volunteer_profile(user: VolunteerUser, db: DbSession) -> VolunteerProfile:
    return svc.get_profile_or_404(db, VolunteerProfile, user)


@router.put("/volunteers/me", response_model=VolunteerProfileOut, responses=error_responses(409))
def save_volunteer_profile(body: VolunteerProfileIn, user: VolunteerUser, db: DbSession) -> VolunteerProfile:
    return svc.upsert_volunteer_profile(db, user, body)


@router.get("/volunteers/me/availability", response_model=list[AvailabilitySlot], responses=error_responses(404))
def get_availability(user: VolunteerUser, db: DbSession):  # noqa: ANN201
    return svc.get_profile_or_404(db, VolunteerProfile, user).availability


@router.put("/volunteers/me/availability", response_model=list[AvailabilitySlot], responses=error_responses(409))
def save_availability(body: AvailabilityIn, user: VolunteerUser, db: DbSession):  # noqa: ANN201
    """Replace the weekly schedule. 'Available now' is part of PUT /volunteers/me."""
    return svc.replace_availability(db, user, body).availability
