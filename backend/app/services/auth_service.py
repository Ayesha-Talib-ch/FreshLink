"""Registration, login and admin creation.

Used by the API (app/api/v1/auth.py) and by the create-admin command
(app/cli.py), so the rules exist in exactly one place.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import burn_dummy_verification, hash_password, verify_password
from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.domain.validation import normalize_phone
from app.models.user import User

def _invalid_credentials() -> AppError:
    # One message for "no such user" and "wrong password", so attackers
    # can't use the login form to find out which emails are registered.
    return AppError(401, "INVALID_CREDENTIALS", "Incorrect email/phone or password.")


def _ensure_unique(db: Session, email: str, phone: str) -> None:
    """Friendly 409 errors before inserting (the UNIQUE constraints are the real guard)."""
    if db.scalar(select(User.id).where(User.email == email)):
        raise AppError(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists.")
    if db.scalar(select(User.id).where(User.phone == phone)):
        raise AppError(409, "PHONE_ALREADY_REGISTERED", "An account with this phone number already exists.")


def _insert_user(db: Session, user: User) -> User:
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two sign-ups with the same email at the same moment: both pass the
        # check above, but the database UNIQUE constraint stops the second one.
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
        if "phone" in constraint:
            raise AppError(409, "PHONE_ALREADY_REGISTERED", "An account with this phone number already exists.") from exc
        raise AppError(409, "EMAIL_ALREADY_REGISTERED", "An account with this email already exists.") from exc
    db.refresh(user)
    return user


def register_user(
    db: Session, *, email: str, phone: str, full_name: str, password: str, role: UserRole
) -> User:
    if role == UserRole.ADMIN:  # defence in depth: the schema already blocks this
        raise AppError(403, "ADMIN_REGISTRATION_FORBIDDEN", "Administrator accounts cannot be self-registered.")
    _ensure_unique(db, email, phone)
    user = User(
        email=email,
        phone=phone,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        # New accounts can log in but must be approved by an admin before
        # using role features (Technical Plan section 5.2).
        verification_status=VerificationStatus.NOT_SUBMITTED,
    )
    return _insert_user(db, user)


def create_admin(db: Session, *, email: str, phone: str, full_name: str, password: str) -> User:
    """Create a super admin. Only reachable from the server command line."""
    _ensure_unique(db, email, phone)
    user = User(
        email=email,
        phone=phone,
        full_name=full_name,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
        is_super_admin=True,
        verification_status=VerificationStatus.APPROVED,  # admins need no approval
    )
    return _insert_user(db, user)


def _find_by_identifier(db: Session, identifier: str) -> User | None:
    identifier = identifier.strip()
    if "@" in identifier:
        condition = User.email == identifier  # CITEXT: case-insensitive
    else:
        try:
            condition = User.phone == normalize_phone(identifier)
        except ValueError:
            return None
    return db.scalar(select(User).where(condition, User.deleted_at.is_(None)))


def authenticate(db: Session, *, identifier: str, password: str) -> User:
    user = _find_by_identifier(db, identifier)
    if user is None:
        burn_dummy_verification(password)
        raise _invalid_credentials()

    valid, new_hash = verify_password(password, user.password_hash)
    if not valid:
        raise _invalid_credentials()

    # Status is only revealed AFTER the correct password, so strangers can't
    # probe which accounts are suspended.
    if user.account_status == AccountStatus.SUSPENDED:
        raise AppError(403, "ACCOUNT_SUSPENDED", "Your account has been suspended. Please contact FreshLink support.")
    if user.account_status == AccountStatus.DEACTIVATED:
        raise AppError(403, "ACCOUNT_DEACTIVATED", "Your account has been deactivated. Please contact FreshLink support.")

    if new_hash:
        user.password_hash = new_hash
    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)
    return user
