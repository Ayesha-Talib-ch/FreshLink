"""What the API returns about a user. password_hash and token_version are never included."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import AccountStatus, UserRole, VerificationStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # can be built from a User row

    id: uuid.UUID
    email: str
    phone: str
    full_name: str
    role: UserRole
    account_status: AccountStatus
    verification_status: VerificationStatus
    is_verified: bool
    is_super_admin: bool
    created_at: datetime
    last_login_at: datetime | None
