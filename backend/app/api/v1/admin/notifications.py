"""Admin: announcements and notification categories (spec 4.4.9). Audited."""

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import AppError, error_responses
from app.models.notification import Announcement, NotificationCategory
from app.schemas.notifications import AnnouncementIn, AnnouncementOut, CategoryOut, CategoryUpdateIn
from app.services import notification_service as svc
from app.services.audit_service import record

router = APIRouter(tags=["admin-notifications"])


@router.post("/announcements", response_model=AnnouncementOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(422))
def send_announcement(body: AnnouncementIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Send a message to everyone, to one role, or to selected users (as notifications)."""
    announcement = svc.announce(db, admin, title=body.title, body=body.body, audience_type=body.audience_type,
                                audience_role=body.audience_role, user_ids=body.user_ids)
    record(db, actor=admin, action="announcement.send", entity_type="announcement", entity_id=announcement.id,
           client=client, after={"audience": body.audience_type, "recipients": announcement.recipient_count})
    db.commit()
    return announcement


@router.get("/announcements", response_model=list[AnnouncementOut])
def list_announcements(admin: AdminUser, db: DbSession):  # noqa: ANN201
    return list(db.scalars(select(Announcement).order_by(Announcement.created_at.desc()).limit(100)))


@router.get("/notification-categories", response_model=list[CategoryOut])
def list_categories(admin: AdminUser, db: DbSession):  # noqa: ANN201
    return list(db.scalars(select(NotificationCategory).order_by(NotificationCategory.code)))


@router.put("/notification-categories/{code}", response_model=CategoryOut, responses=error_responses(404))
def update_category(code: str, body: CategoryUpdateIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Switch a category on or off for everyone."""
    category = db.get(NotificationCategory, code)
    if category is None:
        raise AppError(404, "NOT_FOUND", "Notification category not found.")
    before = {"is_enabled": category.is_enabled, "default_push": category.default_push}
    category.is_enabled, category.default_push = body.is_enabled, body.default_push
    record(db, actor=admin, action="notification_category.update", entity_type="notification_category",
           entity_id=None, client=client, reason=code, before=before, after=body.model_dump())
    db.commit()
    db.refresh(category)
    return category
