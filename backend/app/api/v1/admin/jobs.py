"""Background jobs (Stage 9): see what the worker did, and run a job now. Admin only."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import AppError, error_responses
from app.models.job import JobRun
from app.schemas.common import Page
from app.schemas.jobs import JobOut, JobRunOut
from app.services import job_service
from app.services.audit_service import record

router = APIRouter(tags=["admin-jobs"])


def _known(name: str) -> str:
    if name not in job_service.JOBS:
        raise AppError(404, "NOT_FOUND", "Unknown job.", {"known": list(job_service.JOBS)})
    return name


@router.get("/jobs", response_model=list[JobOut])
def jobs(admin: AdminUser, db: DbSession):  # noqa: ANN201
    """Every scheduled job: how often it runs, its last run and its last successful run.
    A job whose last success is old means the worker isn't running."""
    return job_service.overview(db)


@router.get("/jobs/runs", response_model=Page[JobRunOut])
def job_runs(  # noqa: ANN201
    admin: AdminUser,
    db: DbSession,
    job: str | None = None,
    status: Annotated[str | None, Query(pattern="^(success|failed|skipped)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Run history, newest first (kept for 30 days)."""
    query = select(JobRun)
    if job:
        query = query.where(JobRun.job == _known(job))
    if status:
        query = query.where(JobRun.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(db.scalars(query.order_by(JobRun.started_at.desc(), JobRun.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/jobs/{name}/run", response_model=JobRunOut, responses=error_responses(404))
def run_now(name: str, request: Request, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Run a job immediately (e.g. the urgency scan before a demo). If the worker is running the same
    job at this moment, this run is recorded as "skipped" instead of running twice. Audited."""
    state = request.app.state
    run = job_service.run_job(state.engine, state.session_factory, state.storage, _known(name), "admin")
    record(db, actor=admin, action="job.run", entity_type="job", entity_id=run.id, client=client, reason=name,
           after={"status": run.status, "result": run.result})
    db.commit()
    return run
