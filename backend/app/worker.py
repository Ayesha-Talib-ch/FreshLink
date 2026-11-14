"""The background worker (plan 2.1 and M8): a SEPARATE process that runs the scheduled jobs.

    python -m app.worker

Why a separate process: the API may run several copies (workers) at once; a scheduler inside each
copy would run every job several times. Exactly ONE worker may run: it holds a PostgreSQL advisory
lock for its whole life, and a second worker that can't get the lock stops with a message.
Each job also takes its own lock (see job_service.run_job), so "run now" from the admin screen
can't overlap with the schedule."""

import logging
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.storage import LocalStorage
from app.db.session import create_db_engine, create_session_factory
from app.services.job_service import JOBS, run_job

log = logging.getLogger("freshlink.worker")
WORKER_LOCK = 4_242_001  # the single-instance lock number (any fixed number not used elsewhere)


def take_single_instance_lock(engine: Engine) -> Connection | None:
    """Returns an open connection holding the lock, or None when another worker already runs.
    The lock lasts as long as this connection stays open."""
    conn = engine.connect()
    if conn.scalar(select(func.pg_try_advisory_lock(WORKER_LOCK))):
        conn.commit()
        return conn
    conn.close()
    return None


def release_single_instance_lock(conn: Connection) -> None:
    """Unlock explicitly: closing a pooled connection only returns it to the pool, still holding the lock."""
    conn.execute(select(func.pg_advisory_unlock(WORKER_LOCK)))
    conn.commit()
    conn.close()


def build_scheduler(engine: Engine, session_factory: sessionmaker, storage: LocalStorage,
                    ml_artifacts: Path | None = None) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=UTC)
    for name, job in JOBS.items():
        scheduler.add_job(
            run_job, IntervalTrigger(seconds=job.every_seconds), id=name, name=job.description,
            args=[engine, session_factory, storage, name, "schedule"], kwargs={"ml_artifacts": ml_artifacts},
            # most jobs run once at start-up, then on the interval; weekly retraining waits a week
            next_run_time=datetime.now(UTC) + (timedelta(0) if job.run_at_start else timedelta(seconds=job.every_seconds)),
            max_instances=1,  # never overlap with itself
            coalesce=True,  # after a sleep / pause, run once, not once per missed interval
            misfire_grace_time=60,
        )
    return scheduler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    engine = create_db_engine(settings)
    lock = take_single_instance_lock(engine)
    if lock is None:
        print("[x] Another FreshLink worker is already running. Only one may run at a time.")
        engine.dispose()
        return 1
    scheduler = build_scheduler(engine, create_session_factory(engine), LocalStorage(settings.storage_dir),
                                settings.ml_artifacts_dir)
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    print("[OK] FreshLink worker started: " + ", ".join(
        f"{name} every {job.every_seconds // 60} min" for name, job in JOBS.items()) + ". Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        release_single_instance_lock(lock)
        engine.dispose()
    print("[OK] Worker stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
