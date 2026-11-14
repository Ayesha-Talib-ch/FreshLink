"""SQLAlchemy models (one module per area, e.g. user.py, listing.py).

Every model module must be imported here so Alembic can "see" all tables.
"""

from app.db.base import Base
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import (
    ProviderProfile,
    ReceiverFoodPreference,
    ReceiverProfile,
    VolunteerAvailability,
    VolunteerProfile,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.verification import (
    StoredFile,
    VerificationDocument,
    VerificationRequest,
    VerificationRequirement,
)

__all__ = [
    "AuditLog",
    "Base",
    "FoodCategory",
    "FoodListing",
    "ListingStatusHistory",
    "ProviderCategory",
    "ProviderProfile",
    "ReceiverCategory",
    "ReceiverFoodPreference",
    "ReceiverProfile",
    "StoredFile",
    "RefreshToken",
    "User",
    "VerificationDocument",
    "VerificationRequest",
    "VerificationRequirement",
    "VolunteerAvailability",
    "VolunteerProfile",
]
