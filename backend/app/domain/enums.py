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
