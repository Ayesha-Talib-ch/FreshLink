"""Fixed lists of allowed values (stored in the database as text + a CHECK constraint).

StrEnum members behave like plain strings, so UserRole.PROVIDER == "provider".
The website and the mobile app receive exactly these strings from the API.
"""

from enum import StrEnum


class UserRole(StrEnum):
    PROVIDER = "provider"
    RECEIVER = "receiver"
    VOLUNTEER = "volunteer"
    ADMIN = "admin"


# Roles anyone can choose when registering. Admins are created only by the
# create-admin command (and later by a super admin), never by public sign-up.
PUBLIC_ROLES = (UserRole.PROVIDER, UserRole.RECEIVER, UserRole.VOLUNTEER)


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"
    SUSPENDED = "suspended"


class ClientPlatform(StrEnum):
    """Which app a login session belongs to. It only changes HOW the refresh
    token travels (web: httpOnly cookie, mobile: JSON body); the rules are the same."""

    WEB = "web"
    MOBILE = "mobile"


class VerificationStatus(StrEnum):
    """Single source of truth for approval (Technical Plan section 3.4)."""

    NOT_SUBMITTED = "not_submitted"
    PENDING = "pending"
    INFO_REQUESTED = "info_requested"
    APPROVED = "approved"
    REJECTED = "rejected"


class PerishabilityClass(StrEnum):
    """Broad food type. Different classes need different date information
    (spec section 9.6: not every category has identical shelf-life rules)."""

    COOKED = "cooked"
    BAKERY = "bakery"
    FRESH_PRODUCE = "fresh_produce"
    DAIRY = "dairy"
    PACKAGED = "packaged"
    OTHER = "other"


class QuantityUnit(StrEnum):
    """Every quantity carries a unit; quantities in different units are never added
    together (Technical Plan issue H1)."""

    KG = "kg"
    LITRE = "litre"
    PIECE = "piece"
    PACKAGE = "package"
    PORTION = "portion"
    TRAY = "tray"


# Units that can only be whole numbers (you can't donate 2.5 trays).
COUNTABLE_UNITS = frozenset(
    {QuantityUnit.PIECE, QuantityUnit.PACKAGE, QuantityUnit.PORTION, QuantityUnit.TRAY}
)


class VehicleType(StrEnum):
    BICYCLE = "bicycle"
    MOTORBIKE = "motorbike"
    RICKSHAW = "rickshaw"
    CAR = "car"
    VAN = "van"
    OTHER = "other"


class FilePurpose(StrEnum):
    VERIFICATION_DOC = "verification_doc"
    LISTING_IMAGE = "listing_image"  # used from the food listings stage


class VerificationRequestStatus(StrEnum):
    """One submission's state. The user's overall state is users.verification_status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INFO_REQUESTED = "info_requested"


class ListingStatus(StrEnum):
    ACTIVE = "active"  # visible to receivers, can be requested
    PAUSED = "paused"  # temporarily hidden by the provider
    FULLY_ALLOCATED = "fully_allocated"  # nothing left (set by the allocation stage)
    CLOSED = "closed"  # ended by the provider
    EXPIRED = "expired"  # collection deadline passed (set by the urgency/expiry job)
    SUSPENDED = "suspended"  # hidden by an administrator
    REMOVED = "removed"  # deleted by the provider or an administrator (kept for history)


class DistributionType(StrEnum):
    """Spec 4.1: 'free donation or another administrator-approved arrangement'.
    Only free donation is enabled in v1 (Technical Plan issue M5)."""

    FREE_DONATION = "free_donation"


class UrgencyLevel(StrEnum):
    """Date-based prioritisation levels (spec 6.2). NOT a food-safety rating."""

    NORMAL = "normal"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    INCOMPLETE = "incomplete"  # required date information is missing (plan section 1.3)
