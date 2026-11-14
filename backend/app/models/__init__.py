"""SQLAlchemy models (one module per area, e.g. user.py, listing.py).

Every model module must be imported here so Alembic can "see" all tables.
"""

from app.db.base import Base
from app.models.allocation import Allocation, AllocationRun, AllocationRunLine
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory, UrgencyRuleRow
from app.models.listing import FoodListing, ListingStatusHistory
from app.models.profiles import (
    ProviderProfile,
    ReceiverFoodPreference,
    ReceiverProfile,
    VolunteerAvailability,
    VolunteerProfile,
)
from app.models.refresh_token import RefreshToken
from app.models.request import FoodRequest, RequestStatusHistory
from app.models.user import User
from app.models.verification import (
    StoredFile,
    VerificationDocument,
    VerificationRequest,
    VerificationRequirement,
)

__all__ = [
    "Allocation",
    "AllocationRun",
    "AllocationRunLine",
    "AuditLog",
    "Base",
    "FoodCategory",
    "FoodListing",
    "FoodRequest",
    "ListingStatusHistory",
    "ProviderCategory",
    "ProviderProfile",
    "ReceiverCategory",
    "ReceiverFoodPreference",
    "ReceiverProfile",
    "StoredFile",
    "RefreshToken",
    "RequestStatusHistory",
    "UrgencyRuleRow",
    "User",
    "VerificationDocument",
    "VerificationRequest",
    "VerificationRequirement",
    "VolunteerAvailability",
    "VolunteerProfile",
]
