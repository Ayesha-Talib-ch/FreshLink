"""Admin user management (spec 4.4 "User Management"; plan 4.12)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.domain.validation import PASSWORD_MAX_LENGTH, check_password_strength, normalize_phone
from app.schemas.user import UserOut


class AdminUserOut(UserOut):
    profile_name: str | None  # business / organisation name, if the profile exists


class AdminUserDetail(AdminUserOut):
    profile: dict[str, Any] | None  # role-specific details (category, address, capacity, vehicle ...)
    counts: dict[str, int]  # e.g. listings, requests, completed deliveries
    deleted_at: datetime | None


class StatusChangeIn(BaseModel):
    """Re-activating an account: a note is optional."""

    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class ReasonRequiredIn(BaseModel):
    """Suspending or deactivating someone always needs a reason (it goes into the audit log)."""

    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def need_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Please give a reason (at least 5 characters).")
        return value


class HistoryItem(BaseModel):
    at: datetime
    kind: str  # "account.registered", "account.last_login", "verification.<status>", or an audit action
    summary: str
    actor_email: str | None = None
    reason: str | None = None


class AdminCreateIn(BaseModel):
    email: EmailStr
    phone: str = Field(max_length=30)
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    is_super_admin: bool = False

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Please enter the full name.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @model_validator(mode="after")
    def strong_password(self) -> "AdminCreateIn":
        check_password_strength(self.password, email=self.email)
        return self
