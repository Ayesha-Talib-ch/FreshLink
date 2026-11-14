"""Files and verification request/response bodies."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import FilePurpose, UserRole, VerificationRequestStatus, VerificationStatus
from app.schemas.profiles import ProviderProfileOut, ReceiverProfileOut, VolunteerProfileOut
from app.schemas.user import UserOut


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    purpose: FilePurpose
    original_name: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_type: str
    name: str
    description: str | None
    is_required: bool


class SubmittedDocument(BaseModel):
    file_id: uuid.UUID
    document_type: str = Field(min_length=1, max_length=50)


class VerificationSubmitIn(BaseModel):
    documents: list[SubmittedDocument] = Field(max_length=10)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("documents")
    @classmethod
    def no_duplicate_files(cls, value: list[SubmittedDocument]) -> list[SubmittedDocument]:
        ids = [d.file_id for d in value]
        if len(ids) != len(set(ids)):
            raise ValueError("The same file was attached twice.")
        return value


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_type: str
    file: FileOut


class VerificationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: VerificationRequestStatus
    note: str | None
    submitted_at: datetime
    reviewed_at: datetime | None
    review_notes: str | None  # the admin's reason or question, shown to the user
    documents: list[DocumentOut]


class MyVerificationOut(BaseModel):
    """Everything the "Verification" screen needs on both apps, in one call."""

    verification_status: VerificationStatus
    can_submit: bool
    profile_completed: bool
    requirements: list[RequirementOut]
    latest_request: VerificationRequestOut | None


# ------------------------------------------------------------------- admin


class ReviewIn(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class ReasonIn(BaseModel):
    """Reject / request information: a clear reason is mandatory (spec 5.1: 'Reason Shown')."""

    reason: str = Field(max_length=1000)

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 5:
            raise ValueError("Please write a reason the user can understand (at least 5 characters).")
        return value


class EligibilityIn(BaseModel):
    is_eligible: bool
    notes: str | None = Field(default=None, max_length=1000)


class VerificationQueueItem(BaseModel):
    id: uuid.UUID
    status: VerificationRequestStatus
    submitted_at: datetime
    reviewed_at: datetime | None
    user_id: uuid.UUID
    full_name: str
    email: str
    role: UserRole
    document_count: int


class HistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: VerificationRequestStatus
    submitted_at: datetime
    reviewed_at: datetime | None
    review_notes: str | None


class VerificationAdminOut(VerificationRequestOut):
    user: UserOut
    provider_profile: ProviderProfileOut | None = None
    receiver_profile: ReceiverProfileOut | None = None
    volunteer_profile: VolunteerProfileOut | None = None
    requirements: list[RequirementOut]
    history: list[HistoryItem]  # this user's earlier submissions, newest first
