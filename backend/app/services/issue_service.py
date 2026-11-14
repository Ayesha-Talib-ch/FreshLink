"""Problem reports: "report a problem" for every role, reviewed and resolved by administrators
(spec 4.2.5 / 4.3.3 / 4.4.6 / 4.4.8; plan 3.3 and 4.8).

Only people involved in an item can report on it, so nobody can probe other people's records.
FreshLink never judges whether food is safe: a "food condition" report is passed to an
administrator, who contacts the people involved (plan 1.3)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import IssueCategory, IssueEntityType, IssueStatus, ListingStatus, UserRole
from app.models.assignment import VolunteerAssignment
from app.models.collection import Collection
from app.models.issue import IssueReport
from app.models.listing import FoodListing
from app.models.request import FoodRequest
from app.models.user import User
from app.services import notification_service
from app.services.audit_service import ClientInfo, record

ENTITY_LABELS = {IssueEntityType.LISTING: "listing", IssueEntityType.REQUEST: "request",
                 IssueEntityType.COLLECTION: "collection", IssueEntityType.ASSIGNMENT: "delivery task"}


def _is_volunteer_on(db: Session, user: User, collection_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).select_from(VolunteerAssignment).where(
        VolunteerAssignment.collection_id == collection_id,
        VolunteerAssignment.volunteer.has(user_id=user.id))) > 0


def _describe(db: Session, user: User, entity_type: IssueEntityType, entity_id: uuid.UUID) -> str | None:
    """A short name of the item if the user is involved in it, else None (reported as 404)."""
    if entity_type == IssueEntityType.LISTING:
        listing = db.get(FoodListing, entity_id)
        if listing is None:
            return None
        involved = (listing.provider.user_id == user.id
                    # receivers who requested it, or who can currently see it while browsing
                    or db.scalar(select(func.count()).select_from(FoodRequest).where(
                        FoodRequest.listing_id == listing.id, FoodRequest.receiver.has(user_id=user.id))) > 0
                    or (user.role == UserRole.RECEIVER and user.is_verified and listing.status == ListingStatus.ACTIVE))
        return listing.food_name if involved else None
    if entity_type == IssueEntityType.REQUEST:
        request = db.get(FoodRequest, entity_id)
        if request is None or user.id not in (request.receiver.user_id, request.listing.provider.user_id):
            return None
        return f"{request.listing.food_name} for {request.receiver.receiver_name}"
    if entity_type == IssueEntityType.COLLECTION:
        collection = db.get(Collection, entity_id)
        if collection is None or not (user.id in (collection.provider.user_id, collection.receiver.user_id)
                                      or _is_volunteer_on(db, user, collection.id)):
            return None
        return f"{collection.listing.food_name} for {collection.receiver.receiver_name}"
    task = db.get(VolunteerAssignment, entity_id)
    if task is None:
        return None
    people = {task.collection.provider.user_id, task.collection.receiver.user_id}
    if task.volunteer is not None:
        people.add(task.volunteer.user_id)
    return f"delivery of {task.collection.listing.food_name}" if user.id in people else None


def create(db: Session, user: User, entity_type: IssueEntityType, entity_id: uuid.UUID, category: IssueCategory,
           description: str) -> IssueReport:
    if user.role == UserRole.ADMIN:
        raise AppError(403, "FORBIDDEN", "Administrators resolve problem reports; they don't file them.")
    subject = _describe(db, user, entity_type, entity_id)
    if subject is None:
        raise AppError(404, "NOT_FOUND", f"{ENTITY_LABELS[entity_type].capitalize()} not found.")
    issue = IssueReport(reporter_id=user.id, entity_type=entity_type, entity_id=entity_id, category=category,
                        description=description)
    db.add(issue)
    try:
        db.flush()
    except IntegrityError as exc:  # the partial unique index: one unresolved report per person per item
        db.rollback()
        raise AppError(409, "DUPLICATE_ISSUE", "You already reported a problem with this item. "
                       "An administrator is looking at it.") from exc
    notification_service.notify(
        db, notification_service.admin_ids(db), "admin_alert",
        f"Problem reported: {ENTITY_LABELS[entity_type]}",
        f"{user.full_name} ({user.role.value}) reported '{category.value.replace('_', ' ')}' about {subject}.",
        "issue", issue.id)
    db.commit()
    db.refresh(issue)
    return _decorate(db, [issue])[0]


def _decorate(db: Session, issues: list[IssueReport]) -> list[IssueReport]:
    users = {u.id: u for u in db.scalars(select(User).where(
        User.id.in_({i.reporter_id for i in issues} | {i.resolved_by for i in issues if i.resolved_by})))}
    for i in issues:
        reporter = users.get(i.reporter_id)
        i.reporter_name = reporter.full_name if reporter else None
        i.reporter_role = reporter.role if reporter else None
        resolver = users.get(i.resolved_by) if i.resolved_by else None
        i.resolved_by_email = resolver.email if resolver else None
    return issues


def _page(db: Session, query, page: int, page_size: int) -> dict[str, Any]:  # noqa: ANN001
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(db.scalars(query.order_by(IssueReport.created_at.desc(), IssueReport.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    return {"items": _decorate(db, items), "total": total, "page": page, "page_size": page_size}


def list_mine(db: Session, user: User, *, status: IssueStatus | None, page: int, page_size: int) -> dict[str, Any]:
    query = select(IssueReport).where(IssueReport.reporter_id == user.id)
    if status is not None:
        query = query.where(IssueReport.status == status)
    return _page(db, query, page, page_size)


def get(db: Session, user: User, issue_id: uuid.UUID) -> IssueReport:
    issue = db.get(IssueReport, issue_id)
    if issue is None or not (user.role == UserRole.ADMIN or issue.reporter_id == user.id):
        raise AppError(404, "NOT_FOUND", "Problem report not found.")
    return _decorate(db, [issue])[0]


def list_all(db: Session, *, status: IssueStatus | None, entity_type: IssueEntityType | None,
             category: IssueCategory | None, unresolved_only: bool, page: int, page_size: int) -> dict[str, Any]:
    query = select(IssueReport)
    if status is not None:
        query = query.where(IssueReport.status == status)
    if unresolved_only:
        query = query.where(or_(IssueReport.status == IssueStatus.OPEN, IssueReport.status == IssueStatus.IN_REVIEW))
    if entity_type is not None:
        query = query.where(IssueReport.entity_type == entity_type)
    if category is not None:
        query = query.where(IssueReport.category == category)
    return _page(db, query, page, page_size)


# open -> in_review -> resolved; open -> resolved; a resolved report can be re-opened
_ALLOWED = {IssueStatus.OPEN: {IssueStatus.IN_REVIEW, IssueStatus.RESOLVED},
            IssueStatus.IN_REVIEW: {IssueStatus.OPEN, IssueStatus.RESOLVED},
            IssueStatus.RESOLVED: {IssueStatus.IN_REVIEW}}


def update(db: Session, admin: User, issue_id: uuid.UUID, status: IssueStatus, note: str | None,
           client: ClientInfo) -> IssueReport:
    issue = db.scalar(select(IssueReport).where(IssueReport.id == issue_id).with_for_update(of=IssueReport))
    if issue is None:
        raise AppError(404, "NOT_FOUND", "Problem report not found.")
    if status not in _ALLOWED[issue.status]:
        raise AppError(409, "INVALID_STATUS_TRANSITION",
                       f"A report that is '{issue.status.value}' can't be changed to '{status.value}'.",
                       {"current_status": issue.status.value, "requested_status": status.value})
    if status == IssueStatus.RESOLVED and not note:
        raise AppError(422, "RESOLUTION_NOTE_REQUIRED", "Explain how the problem was resolved (the reporter sees it).",
                       {"fields": [{"field": "body.resolution_note", "message": "Required when resolving."}]})
    before = issue.status
    issue.status = status
    if status == IssueStatus.RESOLVED:
        issue.resolution_note, issue.resolved_by, issue.resolved_at = note, admin.id, datetime.now(UTC)
        notification_service.notify(db, [issue.reporter_id], "admin_alert", "Your problem report was resolved",
                                    note, "issue", issue.id)
    else:
        if note:
            issue.resolution_note = note
        if before == IssueStatus.RESOLVED:  # re-opened
            issue.resolved_by = issue.resolved_at = None
    try:
        db.flush()
    except IntegrityError as exc:  # re-opening while the same person has a newer open report on the item
        db.rollback()
        raise AppError(409, "DUPLICATE_ISSUE", "The reporter has a newer open report about this item. "
                       "Handle that one instead.") from exc
    record(db, actor=admin, action="issue.update", entity_type="issue", entity_id=issue.id, client=client,
           reason=note, before={"status": before.value}, after={"status": status.value})
    db.commit()
    db.refresh(issue)
    return _decorate(db, [issue])[0]
