"""Private file upload and download (Technical Plan section 4.3).

Both apps upload with multipart/form-data (the standard way to send files):
the website from an <input type="file">, the mobile app from expo-image-picker
or expo-document-picker.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import AppSettings, CurrentUser, DbSession, Storage
from app.core.errors import error_responses
from app.domain.enums import FilePurpose
from app.schemas.verification import FileOut
from app.services import file_service

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "", response_model=FileOut, status_code=status.HTTP_201_CREATED,
    responses=error_responses(409, 413, 415),
)
async def upload_file(
    file: Annotated[UploadFile, File(description="PDF, JPG or PNG, up to 5 MB")],
    purpose: Annotated[FilePurpose, Form()],
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    storage: Storage,
) -> FileOut:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)  # read at most 1 byte more than allowed
    stored = file_service.save_upload(
        db, storage, user, purpose=purpose, data=data, original_name=file.filename,
        max_bytes=max_bytes, max_files=settings.max_files_per_user,
    )
    return FileOut.model_validate(stored)


@router.get("/{file_id}", response_class=FileResponse, responses=error_responses(404))
def download_file(file_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: Storage) -> FileResponse:
    """Only the file's owner and administrators can download it."""
    stored = file_service.get_readable_file(db, user, file_id)
    return FileResponse(
        storage.path_for(stored.storage_key),
        media_type=stored.mime_type,
        filename=stored.original_name,
        content_disposition_type="inline",  # PDFs/images open in the browser viewer
        headers={
            "X-Content-Type-Options": "nosniff",  # the browser must trust our checked type
            "Cache-Control": "private, no-store",  # never cached by shared proxies
        },
    )
