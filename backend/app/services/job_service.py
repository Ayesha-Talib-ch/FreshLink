"""Background jobs (plan M8 and 2.1 "worker process"). Run by the worker on a schedule, by an
administrator ("run now") or from the command line.

Every job is idempotent: running it twice in a row changes nothing the second time. A PostgreSQL
advisory lock makes sure the same job never runs twice AT THE SAME TIME (e.g. the worker's
schedule and an admin's "run now"); the second attempt is recorded as "skipped"."""

import logging
import traceback
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.storage import LocalStorage
from app.domain.enums import AssignmentStatus, CollectionStatus, FilePurpose
from app.domain.formatting import format_quantity
from app.models.allocation import Allocation
from app.models.assignment import VolunteerAssignment
from app.models.collection import Collection
from app.models.job import JobRun
from app.models.listing import FoodListing
from app.models.refresh_token import RefreshToken
from app.models.verification import StoredFile, VerificationDocument
from app.services import notification_service, urgency_service

log = logging.getLogger("freshlink.jobs")
PKT = timezone(timedelta(hours=5), "PKT")

REMIND_BEFORE = timedelta(minutes=60)
TOKEN_GRACE = timedelta(days=1)  # expired refresh tokens are kept one more day, then deleted
FILE_GRACE = timedelta(hours=24)  # an upload has a day to be attached to a listing or a document
KEEP_JOB_HISTORY = timedelta(days=30)
# volunteer task statuses before the food is picked up: the volunteer still needs the reminder
BEFORE_PICKUP = (AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE_PICKUP)


@dataclass(frozen=True)
class JobContext:
    storage: LocalStorage
    now: datetime


# ===================================================================== the jobs


def urgency_scan(db: Session, ctx: JobContext) -> dict[str, Any]:
    """Expire listings whose deadline passed, store urgency levels, alert providers (Stage 3b + 7)."""
    return urgency_service.scan(db, ctx.now)


def collection_reminders(db: Session, ctx: JobContext) -> dict[str, Any]:
    """Spec 13 "collection reminder": one hour before a pickup (the agreed time, or the end of the
    pickup window when no time was agreed) remind the receiver, the provider and the volunteer. Once."""
    due = func.coalesce(Collection.scheduled_pickup_at, FoodListing.pickup_window_end)
    collections = db.scalars(
        select(Collection).join(Allocation, Allocation.id == Collection.allocation_id)
        .join(FoodListing, FoodListing.id == Allocation.listing_id)
        .where(Collection.status == CollectionStatus.SCHEDULED, Collection.reminder_sent_at.is_(None),
               due > ctx.now, due <= ctx.now + REMIND_BEFORE)
        .with_for_update(of=Collection, skip_locked=True)).unique().all()
    for c in collections:
        listing = c.listing
        when = (c.scheduled_pickup_at or listing.pickup_window_end).astimezone(PKT)
        what = f"{format_quantity(c.approved_quantity, listing.unit)} of {listing.food_name}"
        label = "Pickup" if c.scheduled_pickup_at else "The pickup window closes"
        people = [c.receiver.user_id, c.provider.user_id]
        task = db.scalar(select(VolunteerAssignment).where(
            VolunteerAssignment.collection_id == c.id, VolunteerAssignment.status.in_(BEFORE_PICKUP)))
        if task is not None and task.volunteer is not None:
            people.append(task.volunteer.user_id)
        notification_service.notify(db, people, "collection_update", "Pickup reminder",
                                    f"{label} at {when:%H:%M} (Pakistan time): {what} from "
                                    f"{c.provider.business_name} for {c.receiver.receiver_name}.",
                                    "collection", c.id)
        c.reminder_sent_at = ctx.now
    db.commit()
    return {"reminded": len(collections)}


def token_cleanup(db: Session, ctx: JobContext) -> dict[str, Any]:
    """Delete refresh tokens that expired more than a day ago (DECISIONS follow-up). Revoked tokens that
    haven't expired are KEPT: they are how a stolen, reused token is recognised. Also prunes old job runs."""
    tokens = db.execute(delete(RefreshToken).where(RefreshToken.expires_at < ctx.now - TOKEN_GRACE)).rowcount
    runs = db.execute(delete(JobRun).where(JobRun.started_at < ctx.now - KEEP_JOB_HISTORY)).rowcount
    db.commit()
    return {"tokens_deleted": tokens, "old_job_runs_deleted": runs}


def file_cleanup(db: Session, ctx: JobContext) -> dict[str, Any]:
    """Uploads that were never attached (after 24 h), and files on disk with no database row
    (left behind when a user was deleted). Database rows go first; files only after the commit."""
    old = ctx.now - FILE_GRACE
    used_images = select(FoodListing.image_file_id).where(FoodListing.image_file_id.is_not(None))
    used_docs = select(VerificationDocument.file_id)
    orphans = list(db.scalars(select(StoredFile).where(StoredFile.created_at < old, (
        ((StoredFile.purpose == FilePurpose.LISTING_IMAGE) & StoredFile.id.not_in(used_images))
        | ((StoredFile.purpose == FilePurpose.VERIFICATION_DOC) & StoredFile.id.not_in(used_docs))))))
    keys = [f.storage_key for f in orphans]
    for f in orphans:
        db.delete(f)
    db.commit()
    for key in keys:
        ctx.storage.delete(key)

    known = set(db.scalars(select(StoredFile.storage_key)))
    left_behind = 0
    root: Path = ctx.storage.root
    if root.exists():
        for path in root.glob("*/*"):
            if (path.is_file() and path.name not in known and len(path.name) == 32
                    and datetime.fromtimestamp(path.stat().st_mtime, UTC) < old):
                path.unlink(missing_ok=True)
                left_behind += 1
    return {"unattached_uploads_deleted": len(keys), "files_without_record_deleted": left_behind}


@dataclass(frozen=True)
class Job:
    run: Callable[[Session, JobContext], dict[str, Any]]
    every_seconds: int
    description: str


JOBS: dict[str, Job] = {
    "urgency_scan": Job(urgency_scan, 5 * 60, "Expire overdue listings and refresh urgency levels"),
    "collection_reminders": Job(collection_reminders, 5 * 60, "Remind people one hour before a pickup"),
    "token_cleanup": Job(token_cleanup, 24 * 3600, "Delete long-expired login sessions and old job history"),
    "file_cleanup": Job(file_cleanup, 24 * 3600, "Delete uploads never attached and files left by deleted users"),
}


# ===================================================================== the runner


def _lock_key(name: str) -> int:
    return zlib.crc32(f"freshlink-job:{name}".encode())  # a stable number per job


def run_job(engine: Engine, session_factory: sessionmaker, storage: LocalStorage, name: str,
            trigger: str, now: datetime | None = None) -> JobRun:
    """Run one job and record it in job_runs. Never raises: failures are recorded, not thrown,
    so one broken job can't stop the worker."""
    job = JOBS[name]
    started = datetime.now(UTC)
    with engine.connect() as lock:
        got = lock.scalar(select(func.pg_try_advisory_lock(_lock_key(name))))
        lock.commit()
        try:
            with session_factory() as db:
                if not got:
                    run = JobRun(job=name, trigger=trigger, status="skipped", started_at=started, finished_at=started,
                                 error="The same job is already running.")
                else:
                    try:
                        result = job.run(db, JobContext(storage=storage, now=now or started))
                        run = JobRun(job=name, trigger=trigger, status="success", result=result,
                                     started_at=started, finished_at=datetime.now(UTC))
                    except Exception as exc:  # noqa: BLE001 - recorded and logged, never re-raised
                        db.rollback()
                        log.exception("Job %s failed", name)
                        run = JobRun(job=name, trigger=trigger, status="failed", started_at=started,
                                     finished_at=datetime.now(UTC),
                                     error=f"{type(exc).__name__}: {exc}\n" + traceback.format_exc(limit=3)[-1500:])
                db.add(run)
                db.commit()
                db.refresh(run)
                db.expunge(run)
                return run
        finally:
            if got:
                lock.execute(select(func.pg_advisory_unlock(_lock_key(name))))
                lock.commit()


def overview(db: Session) -> list[dict[str, Any]]:
    """Each job with its schedule and its latest run / latest successful run."""
    out = []
    for name, job in JOBS.items():
        last = db.scalar(select(JobRun).where(JobRun.job == name).order_by(JobRun.started_at.desc()).limit(1))
        ok = db.scalar(select(JobRun.finished_at).where(JobRun.job == name, JobRun.status == "success")
                       .order_by(JobRun.started_at.desc()).limit(1))
        out.append({"name": name, "description": job.description, "every_minutes": job.every_seconds // 60,
                    "last_run": last, "last_success_at": ok})
    return out
