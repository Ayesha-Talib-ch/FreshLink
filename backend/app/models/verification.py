"""Uploaded files, verification requirements and verification requests
(Technical Plan section 3.3, "Verification and files").

Flow:
  1. The user uploads documents      -> files
  2. The user submits them           -> verification_requests + verification_documents
  3. An admin approves/rejects/asks  -> verification_requests.status + users.verification_status
Every submission is kept, so the full history of reviews is visible (spec 9.12).
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import FilePurpose, UserRole, VerificationRequestStatus
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class StoredFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata of an uploaded file. The bytes live in file storage (local disk in
    development), never in a public folder; they are served only by GET /files/{id}
    after an authorization check."""

    __tablename__ = "files"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[FilePurpose] = mapped_column(str_enum(FilePurpose, "file_purpose"))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)  # random name on disk
    original_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    is_private: Mapped[bool] = mapped_column(Boolean, server_default=true())


class VerificationRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Which documents each role (and receiver category) must upload.
    Admin-configurable later; drives the upload form on both apps."""

    __tablename__ = "verification_requirements"

    role: Mapped[UserRole] = mapped_column(str_enum(UserRole, "requirement_role"))
    # NULL = applies to every receiver category (or to providers/volunteers).
    receiver_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("receiver_categories.id", ondelete="CASCADE")
    )
    document_type: Mapped[str] = mapped_column(String(50))  # e.g. "registration_certificate"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_required: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class VerificationRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "verification_requests"
    __table_args__ = (
        # At most ONE pending submission per user, enforced by the database
        # (protects against double-clicks and two devices submitting at once).
        Index("uq_verification_requests_one_pending_per_user", "user_id", unique=True,
              postgresql_where=text("status = 'pending'")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[VerificationRequestStatus] = mapped_column(
        str_enum(VerificationRequestStatus, "verification_request_status"),
        server_default=VerificationRequestStatus.PENDING.value,
    )
    note: Mapped[str | None] = mapped_column(Text)  # optional message from the user
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    review_notes: Mapped[str | None] = mapped_column(Text)  # reason / requested info, shown to the user
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list["VerificationDocument"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="VerificationDocument.document_type"
    )


class VerificationDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "verification_documents"

    verification_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("verification_requests.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    document_type: Mapped[str] = mapped_column(String(50))

    file: Mapped[StoredFile] = relationship(lazy="joined")
