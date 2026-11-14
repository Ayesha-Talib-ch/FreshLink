"""SQLAlchemy models (one module per area, e.g. user.py, listing.py).

Every model module must be imported here so Alembic can "see" all tables.
"""

from app.db.base import Base
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory
from app.models.profiles import (
    ProviderProfile,
    ReceiverFoodPreference,
    ReceiverProfile,
    VolunteerAvailability,
    VolunteerProfile,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "Base",
    "FoodCategory",
    "ProviderCategory",
    "ProviderProfile",
    "ReceiverCategory",
    "ReceiverFoodPreference",
    "ReceiverProfile",
    "RefreshToken",
    "User",
    "VolunteerAvailability",
    "VolunteerProfile",
]
