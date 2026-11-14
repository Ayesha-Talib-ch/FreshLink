"""Volunteer assignment endpoints (Technical Plan section 4.9), same for website and app."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AdminUser, Client, CurrentUser, DbSession, require_roles, require_verified
from app.core.errors import error_responses
from app.domain.enums import AssignmentStatus, UserRole
from app.models.user import User
from app.schemas.assignments import AssignIn, AssignmentHistoryOut, AssignmentOut, StepIn, SuggestionsOut
from app.schemas.common import Page
from app.schemas.verification import ReasonIn
from app.services import assignment_service as svc

router = APIRouter(prefix="/assignments", tags=["assignments"])


def _verified_volunteer(user: Annotated[User, Depends(require_roles(UserRole.VOLUNTEER))]) -> User:
    return require_verified(user)


Volunteer = Annotated[User, Depends(_verified_volunteer)]


@router.get("/open", response_model=list[AssignmentOut], responses=error_responses(409))
def open_tasks(user: Volunteer, db: DbSession) -> list[dict]:
    """Delivery tasks you can claim: pickup inside your service area, nearest first.
    The receiver's address and phone numbers are shown only after you accept."""
    return svc.open_tasks(db, user)


@router.get("", response_model=Page[AssignmentOut])
def list_tasks(user: CurrentUser, db: DbSession, status: AssignmentStatus | None = None,
               page: Annotated[int, Query(ge=1)] = 1,
               page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> dict:
    """Volunteers: your tasks. Providers/receivers: deliveries of your food. Admins: all."""
    return svc.list_tasks(db, user, status=status, page=page, page_size=page_size)


@router.get("/{task_id}", response_model=AssignmentOut, responses=error_responses(404))
def get_task(task_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict:
    return svc.get_task(db, user, task_id)


@router.get("/{task_id}/history", response_model=list[AssignmentHistoryOut], responses=error_responses(404))
def task_history(task_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    return svc.history(db, user, task_id)


@router.post("/{task_id}/claim", response_model=AssignmentOut, responses=error_responses(404, 409))
def claim(task_id: uuid.UUID, user: Volunteer, db: DbSession) -> dict:
    """Take an open task (you accept it at the same time)."""
    return svc.claim(db, user, task_id)


@router.post("/{task_id}/accept", response_model=AssignmentOut, responses=error_responses(404, 409))
def accept(task_id: uuid.UUID, user: Volunteer, db: DbSession) -> dict:
    """Accept a task an administrator assigned to you."""
    return svc.accept(db, user, task_id)


@router.post("/{task_id}/decline", response_model=AssignmentOut, responses=error_responses(404, 409))
def decline(task_id: uuid.UUID, body: ReasonIn, user: Volunteer, db: DbSession) -> dict:
    """Decline an assigned task (with a reason). The task is offered to other volunteers."""
    return svc.decline(db, user, task_id, body.reason)


@router.post("/{task_id}/status", response_model=AssignmentOut, responses=error_responses(404, 409))
def update_status(task_id: uuid.UUID, body: StepIn, user: Volunteer, db: DbSession, client: Client) -> dict:
    """Report progress: en_route_pickup -> collected (with quantity) -> en_route_dropoff -> delivered,
    or failed (with a reason in note). The receiver's receipt completes the task."""
    return svc.step(db, user, task_id, AssignmentStatus(body.status), body.note, body.quantity, client)


@router.get("/{task_id}/suggested-volunteers", response_model=SuggestionsOut, responses=error_responses(404))
def suggested_volunteers(task_id: uuid.UUID, admin: AdminUser, db: DbSession) -> dict:
    """Admins: volunteers ranked by distance, availability, vehicle, workload and reliability."""
    return svc.suggestions(db, task_id)


@router.post("/{task_id}/assign", response_model=AssignmentOut, responses=error_responses(404, 409))
def assign(task_id: uuid.UUID, body: AssignIn, admin: AdminUser, db: DbSession, client: Client) -> dict:
    """Admins: assign an open task, or reassign one that hasn't started (audited)."""
    return svc.assign(db, admin, task_id, body.volunteer_id, client)


@router.post("/{task_id}/cancel", response_model=AssignmentOut, responses=error_responses(404, 409))
def cancel(task_id: uuid.UUID, body: ReasonIn, admin: AdminUser, db: DbSession, client: Client) -> dict:
    """Admins: stop using a volunteer for this collection (it becomes a self pickup)."""
    return svc.cancel(db, admin, task_id, body.reason, client)
