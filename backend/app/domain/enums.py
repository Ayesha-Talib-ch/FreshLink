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


class VerificationStatus(StrEnum):
    """Single source of truth for approval (Technical Plan section 3.4)."""

    NOT_SUBMITTED = "not_submitted"
    PENDING = "pending"
    INFO_REQUESTED = "info_requested"
    APPROVED = "approved"
    REJECTED = "rejected"
