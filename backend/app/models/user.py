"""The users table: one row per account, for all four roles (Technical Plan section 3.3).

Role-specific details (business name, receiver category, volunteer availability ...)
go into separate profile tables in the next stage, linked by user_id.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, false, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Database-level safety net: only an admin can ever be a super admin.
        CheckConstraint("NOT is_super_admin OR role = 'admin'", name="super_admin_is_admin"),
    )

    # CITEXT: case-insensitive, so Ali@Mail.com and ali@mail.com can't both register.
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    # Stored normalised (+923001234567); unique so one number = one account.
    phone: Mapped[str] = mapped_column(String(16), unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    # Only the Argon2 hash is stored, never the password itself.
    password_hash: Mapped[str] = mapped_column(String(255))

    role: Mapped[UserRole] = mapped_column(str_enum(UserRole, "user_role"))
    account_status: Mapped[AccountStatus] = mapped_column(
        str_enum(AccountStatus, "account_status"),
        server_default=AccountStatus.ACTIVE.value,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        str_enum(VerificationStatus, "verification_status"),
        server_default=VerificationStatus.NOT_SUBMITTED.value,
    )

    is_super_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Increasing this number instantly invalidates every token issued before
    # (used when an account is suspended or the password changes).
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Soft delete: the row stays for history; the account just stops working.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.APPROVED

    def __repr__(self) -> str:  # never include the password hash
        return f"<User {self.email} role={self.role}>"
