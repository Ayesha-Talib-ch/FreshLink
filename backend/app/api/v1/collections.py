"""Collection endpoints (Technical Plan section 4.8), the same for website and app."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import Client, CurrentUser, DbSession
from app.core.errors import error_responses
from app.domain.enums import CollectionStatus
from app.schemas.collections import CollectionOut, HandoverIn, ReceiptIn, ScheduleIn
from app.schemas.common import Page
from app.schemas.verification import ReasonIn
from app.services import collection_service as svc

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get("", response_model=Page[CollectionOut])
def list_collections(
    user: CurrentUser,
    db: DbSession,
    status: CollectionStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Providers: collections of their food. Receivers: food coming to them. Admins: all."""
    return svc.list_collections(db, user, status=status, page=page, page_size=page_size)


@router.get("/{collection_id}", response_model=CollectionOut, responses=error_responses(404))
def get_collection(collection_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    """Collection instructions with both sides' contact details (shared only here)."""
    return svc.get_involved(db, user, collection_id)


@router.patch("/{collection_id}", response_model=CollectionOut, responses=error_responses(404, 409))
def schedule_collection(collection_id: uuid.UUID, body: ScheduleIn, user: CurrentUser, db: DbSession):  # noqa: ANN201
    """Set the method (self pickup / volunteer) or the pickup time, before the handover."""
    return svc.schedule(db, user, collection_id, body.method, body.scheduled_pickup_at)


@router.post("/{collection_id}/handover", response_model=CollectionOut, responses=error_responses(404, 409))
def confirm_handover(collection_id: uuid.UUID, body: HandoverIn, user: CurrentUser, db: DbSession,
                     client: Client):  # noqa: ANN201
    """Provider: confirm the quantity actually handed over (up to the approved quantity)."""
    return svc.handover(db, user, collection_id, body.quantity, body.notes, client)


@router.post("/{collection_id}/receipt", response_model=CollectionOut, responses=error_responses(404, 409))
def confirm_receipt(collection_id: uuid.UUID, body: ReceiptIn, user: CurrentUser, db: DbSession,
                    client: Client):  # noqa: ANN201
    """Receiver: confirm the quantity actually received (up to what was handed over).
    This completes the redistribution."""
    return svc.receipt(db, user, collection_id, body.quantity, body.notes, body.reason, client)


@router.post("/{collection_id}/fail", response_model=CollectionOut, responses=error_responses(404, 409))
def report_failure(collection_id: uuid.UUID, body: ReasonIn, user: CurrentUser, db: DbSession,
                   client: Client):  # noqa: ANN201
    """The collection didn't happen. Food not yet handed over becomes available again."""
    return svc.fail(db, user, collection_id, body.reason, client)
