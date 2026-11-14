"""Notification endpoints for every user (Technical Plan section 4.10), same for web and app.

The apps poll GET /notifications/unread-count every 30 seconds for the bell badge
(plan 2.4 "near-real-time" synchronisation), and refresh the list when it changes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core.errors import error_responses
from app.schemas.common import Page
from app.schemas.notifications import MarkedOut, NotificationOut, UnreadCountOut
from app.services import notification_service as svc

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationOut])
def my_notifications(user: CurrentUser, db: DbSession, unread_only: bool = False, category: str | None = None,
                     page: Annotated[int, Query(ge=1)] = 1,
                     page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
    """Your notifications, newest first. Each has a `link` to the related screen."""
    return svc.list_mine(db, user, unread_only=unread_only, category=category, page=page, page_size=page_size)


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(user: CurrentUser, db: DbSession) -> dict:
    """Cheap call for the bell badge (one indexed count)."""
    return {"unread": svc.unread_count(db, user)}


@router.post("/read-all", response_model=MarkedOut)
def read_all(user: CurrentUser, db: DbSession) -> dict:
    return {"marked": svc.mark_all_read(db, user)}


@router.post("/{notification_id}/read", response_model=NotificationOut, responses=error_responses(404))
def read_one(notification_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    return svc.mark_read(db, user, notification_id)
