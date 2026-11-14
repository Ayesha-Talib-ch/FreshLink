"""Stage 9: background jobs, the job runner (locks, failures, history), the admin job screens and the
worker's scheduler and single-instance lock."""

import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.core.storage import LocalStorage
from app.domain.enums import FilePurpose, UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.collection import Collection
from app.models.job import JobRun
from app.models.refresh_token import RefreshToken
from app.models.verification import StoredFile
from app.services import job_service
from app.services.job_service import JOBS, run_job
from app.worker import WORKER_LOCK, build_scheduler, release_single_instance_lock, take_single_instance_lock
from tests.api.test_assignments import _volunteer, world  # noqa: F401  (fixture)
from tests.api.test_notifications import _request, food, scene  # noqa: F401  (fixtures)
from tests.conftest import MakeUser, login_headers


@pytest.fixture
def runner(client: TestClient):  # noqa: ANN201
    """Run a job the way the worker does, against the test database and the test's storage folder."""
    state = client.app.state

    def run(name: str, now: datetime | None = None) -> JobRun:
        return run_job(state.engine, state.session_factory, state.storage, name, "schedule", now)
    return run


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED).email)


def _titles(client: TestClient, headers: dict) -> list[str]:
    return [n["title"] for n in client.get("/api/v1/notifications?page_size=100", headers=headers).json()["items"]]


# ========================================================================= reminders


def _approved_collection(client: TestClient, scene: dict) -> dict:
    request = _request(client, scene, 10)
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=scene["provider"])
    return client.get("/api/v1/collections", headers=scene["provider"]).json()["items"][0]


def test_reminder_one_hour_before_pickup_once(client: TestClient, scene: dict, runner, db_session: Session) -> None:  # noqa: ANN001
    collection = _approved_collection(client, scene)
    pickup = datetime.now(UTC) + timedelta(hours=2)  # inside the 1 h - 4 h pickup window
    changed = client.patch(f"/api/v1/collections/{collection['id']}", headers=scene["receiver"],
                           json={"scheduled_pickup_at": pickup.isoformat()})
    assert changed.status_code == 200, changed.text
    assert runner("collection_reminders").result == {"reminded": 0}  # 2 h away: not yet
    first = runner("collection_reminders", now=pickup - timedelta(minutes=40))
    assert (first.status, first.result) == ("success", {"reminded": 1})
    note = client.get("/api/v1/notifications", headers=scene["receiver"]).json()["items"][0]
    assert note["title"] == "Pickup reminder" and note["link"] == f"/collections/{collection['id']}"
    assert "10 packages of Fresh bread" in note["body"] and "(Pakistan time)" in note["body"]
    assert "Pickup reminder" in _titles(client, scene["provider"])
    assert runner("collection_reminders", now=pickup - timedelta(minutes=20)).result == {"reminded": 0}  # never twice
    assert _titles(client, scene["receiver"]).count("Pickup reminder") == 1


def test_no_reminder_after_handover(client: TestClient, scene: dict, runner) -> None:  # noqa: ANN001
    collection = _approved_collection(client, scene)  # no agreed time: the window end counts
    client.post(f"/api/v1/collections/{collection['id']}/handover", headers=scene["provider"], json={"quantity": 10})
    window_end = datetime.fromisoformat(scene["listing"]["pickup_window_end"])
    assert runner("collection_reminders", now=window_end - timedelta(minutes=30)).result == {"reminded": 0}


def test_no_reminder_after_the_time_passed(client: TestClient, scene: dict, runner) -> None:  # noqa: ANN001
    _approved_collection(client, scene)
    window_end = datetime.fromisoformat(scene["listing"]["pickup_window_end"])
    assert runner("collection_reminders", now=window_end + timedelta(minutes=5)).result == {"reminded": 0}
    assert runner("collection_reminders", now=window_end - timedelta(minutes=30)).result == {"reminded": 1}


def test_reminder_without_agreed_time_uses_the_window_and_tells_the_volunteer(
        client: TestClient, make_user: MakeUser, world: dict, runner) -> None:  # noqa: ANN001, F811
    _, bilal = _volunteer(client, make_user, "Bilal")
    client.post(f"/api/v1/assignments/{world['task']['id']}/claim", headers=bilal)
    # world's request asked for a pickup time 2 h from now; check 1.5 h from now
    run = runner("collection_reminders", now=datetime.now(UTC) + timedelta(minutes=90))
    assert run.result == {"reminded": 1}
    assert "Pickup reminder" in _titles(client, bilal)


# ================================================================= clean-up jobs


def test_token_cleanup_keeps_what_is_still_needed(client: TestClient, make_user: MakeUser, runner,
                                                  db_session: Session) -> None:  # noqa: ANN001
    user = make_user(role=UserRole.VOLUNTEER)
    for _ in range(3):
        client.post("/api/v1/auth/login", json={"identifier": user.email, "password": "Correct-Horse-42",
                                                "platform": "mobile"})
    tokens = list(db_session.scalars(select(RefreshToken.id)))
    now = datetime.now(UTC)
    db_session.execute(update(RefreshToken).where(RefreshToken.id == tokens[0]).values(expires_at=now - timedelta(days=3)))
    db_session.execute(update(RefreshToken).where(RefreshToken.id == tokens[1]).values(
        expires_at=now - timedelta(hours=2)))  # expired, but within the 1-day grace
    db_session.execute(update(RefreshToken).where(RefreshToken.id == tokens[2]).values(revoked_at=now))  # reuse detection
    db_session.add(JobRun(job="urgency_scan", trigger="schedule", status="success", started_at=now - timedelta(days=40)))
    db_session.commit()
    run = runner("token_cleanup")
    assert run.result == {"tokens_deleted": 1, "old_job_runs_deleted": 1}
    assert set(db_session.scalars(select(RefreshToken.id))) == {tokens[1], tokens[2]}


def test_file_cleanup(client: TestClient, scene: dict, runner, db_session: Session) -> None:  # noqa: ANN001
    storage: LocalStorage = client.app.state.storage
    owner = scene["provider_user"].id
    old = datetime.now(UTC) - timedelta(days=2)

    def stored(purpose: FilePurpose, created) -> StoredFile:  # noqa: ANN001
        key = storage.save(b"%PDF-1.4 test")
        row = StoredFile(owner_user_id=owner, purpose=purpose, storage_key=key, original_name="x.pdf",
                         mime_type="application/pdf", size_bytes=13, created_at=created)
        db_session.add(row)
        db_session.commit()
        return row

    unattached_old = stored(FilePurpose.LISTING_IMAGE, old)
    unattached_new = stored(FilePurpose.VERIFICATION_DOC, datetime.now(UTC))  # still within 24 h
    attached = stored(FilePurpose.LISTING_IMAGE, old)
    db_session.execute(update(job_service.FoodListing).values(image_file_id=attached.id))  # used as the photo
    db_session.commit()
    stray_old = storage.save(b"left by a deleted user")
    stray_new = storage.save(b"being uploaded right now")
    two_days = time.time() - 2 * 86400
    os.utime(storage.path_for(stray_old), (two_days, two_days))

    run = runner("file_cleanup")
    assert run.result == {"unattached_uploads_deleted": 1, "files_without_record_deleted": 1}
    assert not storage.path_for(unattached_old.storage_key).exists()
    assert storage.path_for(unattached_new.storage_key).exists() and storage.path_for(attached.storage_key).exists()
    assert not storage.path_for(stray_old).exists() and storage.path_for(stray_new).exists()
    assert db_session.scalar(select(func.count()).select_from(StoredFile)) == 2
    assert runner("file_cleanup").result == {"unattached_uploads_deleted": 0, "files_without_record_deleted": 0}


def test_urgency_scan_job(client: TestClient, scene: dict, runner) -> None:  # noqa: ANN001
    run = runner("urgency_scan")
    assert run.status == "success" and run.result["checked"] == 1


# ===================================================================== the runner


def test_a_failing_job_is_recorded_not_raised(client: TestClient, runner, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    def broken(db: Session, ctx: job_service.JobContext) -> dict:
        raise RuntimeError("disk full")
    monkeypatch.setitem(JOBS, "token_cleanup", job_service.Job(broken, 60, "broken for the test"))
    run = runner("token_cleanup")
    assert run.status == "failed" and run.error.startswith("RuntimeError: disk full")
    assert runner("urgency_scan").status == "success"  # the next job still runs


def test_the_same_job_never_runs_twice_at_once(client: TestClient, runner, test_db_engine: Engine) -> None:  # noqa: ANN001
    with test_db_engine.connect() as other:  # someone else is running urgency_scan right now
        assert other.scalar(select(func.pg_try_advisory_lock(job_service._lock_key("urgency_scan"))))
        busy = runner("urgency_scan")
        assert busy.status == "skipped" and busy.error == "The same job is already running."
        assert runner("token_cleanup").status == "success"  # other jobs are not blocked
        other.execute(select(func.pg_advisory_unlock(job_service._lock_key("urgency_scan"))))
    assert runner("urgency_scan").status == "success"


# ================================================================= admin endpoints


def test_admin_job_screens(client: TestClient, admin: dict, scene: dict, db_session: Session) -> None:
    jobs = {j["name"]: j for j in client.get("/api/v1/admin/jobs", headers=admin).json()}
    assert set(jobs) == {"urgency_scan", "collection_reminders", "token_cleanup", "file_cleanup", "surplus_retrain"}
    assert jobs["surplus_retrain"]["every_minutes"] == 7 * 24 * 60
    assert jobs["urgency_scan"]["every_minutes"] == 5 and jobs["urgency_scan"]["last_run"] is None

    ran = client.post("/api/v1/admin/jobs/urgency_scan/run", headers=admin)
    assert ran.status_code == 200 and ran.json()["status"] == "success" and ran.json()["trigger"] == "admin"
    after = {j["name"]: j for j in client.get("/api/v1/admin/jobs", headers=admin).json()}
    assert after["urgency_scan"]["last_run"]["id"] == ran.json()["id"] and after["urgency_scan"]["last_success_at"]
    history = client.get("/api/v1/admin/jobs/runs?job=urgency_scan", headers=admin).json()
    assert history["total"] == 1
    assert db_session.scalar(select(AuditLog.reason).where(AuditLog.action == "job.run")) == "urgency_scan"
    assert client.post("/api/v1/admin/jobs/nonsense/run", headers=admin).status_code == 404
    assert client.get("/api/v1/admin/jobs/runs?status=weird", headers=admin).status_code == 422
    assert client.post("/api/v1/admin/jobs/urgency_scan/run", headers=scene["provider"]).status_code == 403


# ====================================================================== the worker


def test_scheduler_has_every_job(client: TestClient) -> None:
    state = client.app.state
    scheduler = build_scheduler(state.engine, state.session_factory, state.storage)
    jobs = {j.id: j for j in scheduler.get_jobs()}
    assert set(jobs) == set(JOBS)
    assert jobs["urgency_scan"].trigger.interval == timedelta(minutes=5)
    assert jobs["file_cleanup"].trigger.interval == timedelta(days=1)
    assert all(j.max_instances == 1 and j.coalesce for j in jobs.values())
    # weekly retraining doesn't run at every worker start-up: its first run is a week away
    assert jobs["surplus_retrain"].next_run_time - datetime.now(UTC) > timedelta(days=6)
    assert jobs["urgency_scan"].next_run_time - datetime.now(UTC) < timedelta(minutes=1)


def test_only_one_worker_may_run(test_db_engine: Engine) -> None:
    first = take_single_instance_lock(test_db_engine)
    assert first is not None
    try:
        assert take_single_instance_lock(test_db_engine) is None  # a second worker is refused
    finally:
        release_single_instance_lock(first)
    again = take_single_instance_lock(test_db_engine)  # after the first stops, a new one may start
    assert again is not None
    release_single_instance_lock(again)
    assert WORKER_LOCK != job_service._lock_key("urgency_scan")
