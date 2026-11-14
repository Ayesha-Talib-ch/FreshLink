"""The user's side of verification: see status and requirements, submit documents.

Unverified users can use these (they need them to GET verified).
"""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.core.errors import error_responses
from app.schemas.verification import MyVerificationOut, VerificationRequestOut, VerificationSubmitIn
from app.services import verification_service as svc

router = APIRouter(prefix="/me/verification", tags=["verification"])


@router.get("", response_model=MyVerificationOut)
def my_verification(user: CurrentUser, db: DbSession) -> dict:
    """Current status, which documents are needed, and the latest submission
    (including the administrator's reason or question)."""
    return svc.my_verification(db, user)


@router.post("", response_model=VerificationRequestOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(409))
def submit_verification(body: VerificationSubmitIn, user: CurrentUser, db: DbSession):  # noqa: ANN201
    """Submit (or resubmit after rejection / an information request) uploaded documents."""
    return svc.submit(db, user, body)
