"""Upload and download rules for private files."""

from pathlib import PurePath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.storage import LocalStorage
from app.domain.enums import FilePurpose, UserRole
from app.models.user import User
from app.models.verification import StoredFile

# A file's TYPE is decided by its first bytes ("magic numbers"), not by its name
# or by what the browser claims. A virus renamed to "cnic.pdf" is still rejected.
_SIGNATURES = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}

ALLOWED_DESCRIPTION = "PDF, JPG or PNG"


def detect_mime_type(data: bytes) -> str | None:
    for signature, mime in _SIGNATURES.items():
        if data.startswith(signature):
            return mime
    return None


def _safe_name(raw: str | None) -> str:
    # Keep only the last part ("C:\fakepath\cnic.pdf" -> "cnic.pdf"); it is only
    # shown as a label, never used as a path on disk.
    name = PurePath((raw or "").replace("\\", "/")).name.strip()
    return (name or "document")[:255]


def save_upload(
    db: Session,
    storage: LocalStorage,
    user: User,
    *,
    purpose: FilePurpose,
    data: bytes,
    original_name: str | None,
    max_bytes: int,
    max_files: int,
) -> StoredFile:
    if user.role == UserRole.ADMIN:
        raise AppError(403, "FORBIDDEN", "Administrators don't upload verification documents.")
    if purpose != FilePurpose.VERIFICATION_DOC:
        raise AppError(422, "PURPOSE_NOT_AVAILABLE", "This kind of upload is not available yet.",
                       {"field": "purpose"})
    if not data:
        raise AppError(422, "EMPTY_FILE", "The file is empty.", {"field": "file"})
    if len(data) > max_bytes:
        raise AppError(413, "FILE_TOO_LARGE", f"The file is too large. Maximum size is {max_bytes // (1024 * 1024)} MB.")

    mime = detect_mime_type(data)
    if mime is None:
        raise AppError(415, "UNSUPPORTED_FILE_TYPE", f"Please upload a {ALLOWED_DESCRIPTION} file.")

    count = db.scalar(select(func.count()).select_from(StoredFile).where(StoredFile.owner_user_id == user.id))
    if count >= max_files:
        raise AppError(409, "TOO_MANY_FILES", "You have uploaded too many files. Please contact support.")

    key = storage.save(data)
    stored = StoredFile(
        owner_user_id=user.id, purpose=purpose, storage_key=key,
        original_name=_safe_name(original_name), mime_type=mime, size_bytes=len(data),
    )
    db.add(stored)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)  # don't leave an orphan file on disk
        raise
    db.refresh(stored)
    return stored


def get_readable_file(db: Session, user: User, file_id) -> StoredFile:  # noqa: ANN001
    """Owner and administrators only. Anyone else gets 404 (not 403), so they
    can't even learn that the file exists (Technical Plan section 5.3)."""
    stored = db.get(StoredFile, file_id)
    if stored is None or (stored.owner_user_id != user.id and user.role != UserRole.ADMIN):
        raise AppError(404, "NOT_FOUND", "File not found.")
    return stored
