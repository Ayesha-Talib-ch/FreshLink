"""Report a problem (plan 4.8): any approved user involved in a listing, request, collection or
delivery task. Administrators are alerted and resolve it under /admin/issues."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, VerifiedUser
from app.core.errors import error_responses
from app.domain.enums import IssueStatus
from app.schemas.admin_monitoring import IssueIn, IssueOut
from app.schemas.common import Page
from app.services import issue_service as svc

router = APIRouter(tags=["issues"])


@router.post("/issues", response_model=IssueOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(403, 404, 409))
def report_problem(body: IssueIn, user: VerifiedUser, db: DbSession):  # noqa: ANN201
    """Only people involved in the item can report on it. One open report per person per item."""
    return svc.create(db, user, body.entity_type, body.entity_id, body.category, body.description)


@router.get("/issues", response_model=Page[IssueOut])
def my_problem_reports(  # noqa: ANN201
    user: CurrentUser,
    db: DbSession,
    status: IssueStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Your own reports and how they were resolved (administrators: see /admin/issues)."""
    return svc.list_mine(db, user, status=status, page=page, page_size=page_size)


@router.get("/issues/{issue_id}", response_model=IssueOut, responses=error_responses(404))
def get_problem_report(issue_id: uuid.UUID, user: CurrentUser, db: DbSession):  # noqa: ANN201
    return svc.get(db, user, issue_id)
