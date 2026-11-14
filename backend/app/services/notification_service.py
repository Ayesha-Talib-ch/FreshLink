"""In-app notifications (spec section 13).

notify() is called by the other services INSIDE their own transaction: if the action is
rolled back, its notification disappears with it; if the action succeeds, the notification
is guaranteed to exist. Nothing is sent outside the app yet (push comes with the mobile app).
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import AccountStatus, UserRole
from app.models.notification import Announcement, Notification, NotificationCategory
from app.models.user import User

# Where each kind of item opens in the apps (website path; the app uses freshlink://<same>).
LINKS = {
    "request": "/requests/{id}",
    "listing": "/listings/{id}",
    "collection": "/collections/{id}",
    "assignment": "/assignments/{id}",
    "allocation": "/allocations/{id}",
    "verification": "/me/verification",
    "user": "/admin/users/{id}",
    "verification_request": "/admin/verifications/{id}",
    "announcement": "/notifications",
    "issue": "/issues/{id}",
}


def link_for(entity_type: str | None, entity_id: uuid.UUID | None) -> str | None:
    template = LINKS.get(entity_type or "")
    return template.format(id=entity_id) if template else None


def notify(db: Session, user_ids: Iterable[uuid.UUID | None], category: str, title: str, body: str,
           entity_type: str | None = None, entity_id: uuid.UUID | None = None) -> int:
    """Create one notification per user (duplicates and None are ignored). Returns how many.
    A category switched off by an administrator sends nothing. The caller commits."""
    enabled = db.scalar(select(NotificationCategory.is_enabled).where(NotificationCategory.code == category))
    if not enabled:
        return 0
    recipients = list(dict.fromkeys(uid for uid in user_ids if uid is not None))
    db.add_all(Notification(user_id=uid, category_code=category, title=title[:150], body=body,
                            entity_type=entity_type, entity_id=entity_id) for uid in recipients)
    return len(recipients)


def admin_ids(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(select(User.id).where(User.role == UserRole.ADMIN, User.deleted_at.is_(None),
                                                 User.account_status == AccountStatus.ACTIVE)))


# ------------------------------------------------------------------ the user's own


def list_mine(db: Session, user: User, *, unread_only: bool, category: str | None, page: int,
              page_size: int) -> dict[str, Any]:
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.is_read.is_(False))
    if category:
        query = query.where(Notification.category_code == category)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Notification.created_at.desc(), Notification.id)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    for n in items:
        n.link = link_for(n.entity_type, n.entity_id)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def unread_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.is_read.is_(False)))


def mark_read(db: Session, user: User, notification_id: uuid.UUID) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:  # other people's: "not found"
        raise AppError(404, "NOT_FOUND", "Notification not found.")
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(UTC)
        db.commit()
        db.refresh(notification)
    notification.link = link_for(notification.entity_type, notification.entity_id)
    return notification


def mark_all_read(db: Session, user: User) -> int:
    result = db.execute(update(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))
                        .values(is_read=True, read_at=datetime.now(UTC)))
    db.commit()
    return result.rowcount


# --------------------------------------------------------------------- admins


def announce(db: Session, admin: User, *, title: str, body: str, audience_type: str, audience_role: UserRole | None,
             user_ids: list[uuid.UUID]) -> Announcement:
    query = select(User.id).where(User.deleted_at.is_(None), User.account_status == AccountStatus.ACTIVE)
    if audience_type == "role":
        if audience_role is None:
            raise AppError(422, "VALIDATION_ERROR", "Choose the role to send the announcement to.",
                           {"field": "audience_role"})
        query = query.where(User.role == audience_role)
    elif audience_type == "users":
        if not user_ids:
            raise AppError(422, "VALIDATION_ERROR", "Choose at least one user.", {"field": "user_ids"})
        query = query.where(User.id.in_(user_ids))
    recipients = list(db.scalars(query))
    if not recipients:
        raise AppError(422, "NO_RECIPIENTS", "Nobody matches this audience.")
    announcement = Announcement(created_by=admin.id, title=title, body=body, audience_type=audience_type,
                                audience_role=audience_role.value if audience_role else None,
                                recipient_count=len(recipients))
    db.add(announcement)
    db.flush()
    sent = notify(db, recipients, "announcement", title, body, "announcement", announcement.id)
    announcement.recipient_count = sent
    db.commit()
    db.refresh(announcement)
    return announcement
