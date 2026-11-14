"""Stage 10a: daily logs (record, list, delete, CSV import), surplus estimates (model and cold start),
estimate history, the model registry and the weekly retraining job."""

import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import joblib
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import LogSource, UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.ml import MlModel, ProviderDailyLog
from app.services import ml_service
from app.services.job_service import run_job
from tests.api.test_listings import _make_provider
from tests.api.test_notifications import food  # noqa: F401  (fixture)
from tests.conftest import MakeUser, login_headers
from tests.unit.test_surplus_ml import synthetic  # noqa: F401  (module fixture: a slice of synthetic data)

PKT = timezone(timedelta(hours=5))
LOGS = "/api/v1/providers/me/daily-logs"
EST = "/api/v1/ai/surplus-estimates"


def today() -> date:
    return datetime.now(PKT).date()


@pytest.fixture(scope="module")
def trained(synthetic):  # noqa: ANN001, ANN201, F811
    from app.ml.surplus_training import train
    return train(synthetic)


@pytest.fixture
def model(client: TestClient, db_session: Session, trained) -> MlModel:  # noqa: ANN001
    """Save the trained model in this test's artifacts folder and register it as active."""
    folder = client.app.state.settings.ml_artifacts_dir
    folder.mkdir(parents=True, exist_ok=True)
    version = f"surplus-test-{uuid.uuid4().hex[:6]}"
    joblib.dump(trained.bundle, folder / f"{version}.joblib")
    row = MlModel(name="surplus", version=version, algorithm=trained.algorithm, artifact=f"{version}.joblib",
                  data_source="synthetic", training_rows=trained.training_rows, metrics=trained.metrics, is_active=True)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser, db_session: Session) -> dict:
    user, headers = _make_provider(client, make_user, db_session)
    return {"user": user, "headers": headers}


def _profile_id(client: TestClient, headers: dict) -> str:
    return client.get("/api/v1/providers/me", headers=headers).json()["id"]


def _seed_logs(db: Session, provider_id: str, category_id: str, days: int, unit: str = "package",
               produced: int = 100, sold: int = 85, end: date | None = None) -> None:
    end = end or today() - timedelta(days=1)
    for i in range(days):
        day = end - timedelta(days=i)
        db.add(ProviderDailyLog(provider_id=provider_id, log_date=day, food_category_id=category_id, unit=unit,
                                produced_qty=produced + (i % 7), sold_qty=sold, surplus_qty=produced + (i % 7) - sold,
                                source=LogSource.MANUAL))
    db.commit()


# ==================================================================== daily logs


def test_record_update_and_rules(client: TestClient, provider: dict, food: dict) -> None:  # noqa: F811
    h = provider["headers"]
    body = {"log_date": (today() - timedelta(days=1)).isoformat(), "food_category_id": food["bakery_items"],
            "unit": "package", "produced_qty": 120, "sold_qty": 95}
    first = client.post(LOGS, headers=h, json=body)
    assert first.status_code == 201 and first.json()["surplus_qty"] == "25.00" and first.json()["source"] == "manual"
    again = client.post(LOGS, headers=h, json={**body, "sold_qty": 130})
    assert again.status_code == 200 and again.json()["surplus_qty"] == "0.00"  # sold more than made: no surplus
    assert client.get(LOGS, headers=h).json()["total"] == 1  # updated, not duplicated

    def code(**changes) -> str:  # noqa: ANN003
        return client.post(LOGS, headers=h, json={**body, **changes}).json()["error"]["code"]

    assert code(log_date=(today() + timedelta(days=1)).isoformat()) == "INVALID_DAILY_LOG"  # future
    assert code(log_date=(today() - timedelta(days=400)).isoformat()) == "INVALID_DAILY_LOG"  # too old
    assert code(unit="kg") == "INVALID_DAILY_LOG"  # this category is logged in packages
    assert code(produced_qty=10.5) == "INVALID_DAILY_LOG"  # packages are whole numbers
    assert code(produced_qty=-1) == "VALIDATION_ERROR"
    assert code(food_category_id=str(uuid.uuid4())) == "VALIDATION_ERROR"


def test_list_delete_and_privacy(client: TestClient, provider: dict, make_user: MakeUser, db_session: Session,
                                 food: dict) -> None:  # noqa: F811
    _seed_logs(db_session, _profile_id(client, provider["headers"]), food["bakery_items"], 10)
    page = client.get(f"{LOGS}?page_size=5", headers=provider["headers"]).json()
    assert page["total"] == 10 and page["items"][0]["log_date"] == (today() - timedelta(days=1)).isoformat()
    since = (today() - timedelta(days=3)).isoformat()
    assert client.get(f"{LOGS}?date_from={since}", headers=provider["headers"]).json()["total"] == 3

    _, other = _make_provider(client, make_user, db_session)
    log_id = page["items"][0]["id"]
    assert client.delete(f"{LOGS}/{log_id}", headers=other).status_code == 404  # not theirs
    assert client.delete(f"{LOGS}/{log_id}", headers=provider["headers"]).status_code == 204
    receiver = login_headers(client, make_user(role=UserRole.RECEIVER).email)
    assert client.get(LOGS, headers=receiver).status_code == 403


def _csv(lines: list[str]) -> dict:
    data = "date,food_category_code,unit,produced,sold\n" + "\n".join(lines) + "\n"
    return {"file": ("logs.csv", io.BytesIO(data.encode()), "text/csv")}


def test_csv_import(client: TestClient, provider: dict) -> None:
    h = provider["headers"]
    days = [(today() - timedelta(days=i)).isoformat() for i in range(1, 4)]
    good = [f"{d},bakery_items,package,100,80" for d in days]
    first = client.post(f"{LOGS}/import", headers=h, files=_csv(good))
    assert first.status_code == 200 and first.json() == {"created": 3, "updated": 0}
    assert client.post(f"{LOGS}/import", headers=h, files=_csv(good)).json() == {"created": 0, "updated": 3}
    logs = client.get(LOGS, headers=h).json()["items"]
    assert {log["source"] for log in logs} == {"import"} and logs[0]["surplus_qty"] == "20.00"

    bad = [f"{days[0]},bakery_items,package,100,80", "2026-02-30,bakery_items,package,1,1",
           f"{days[1]},nonsense,package,1,1", f"{days[0]},bakery_items,package,5,5"]
    refused = client.post(f"{LOGS}/import", headers=h, files=_csv(bad))
    assert refused.status_code == 422 and "nothing was imported" in refused.json()["error"]["message"]
    rows = [e["row"] for e in refused.json()["error"]["details"]["errors"]]
    assert rows == [3, 4, 5]  # bad date, unknown category, the same day twice
    wrong = {"file": ("x.csv", io.BytesIO(b"day,amount\n1,2\n"), "text/csv")}
    assert client.post(f"{LOGS}/import", headers=h, files=wrong).json()["error"]["code"] == "INVALID_FILE"
    huge = {"file": ("x.csv", io.BytesIO(b"x" * (1024 * 1024 + 10)), "text/csv")}
    assert client.post(f"{LOGS}/import", headers=h, files=huge).status_code == 413


# ===================================================================== estimates


def _estimate(client: TestClient, headers: dict, category: str, days_ahead: int = 1, **extra):  # noqa: ANN003, ANN202
    return client.post(EST, headers=headers, json={"food_category_id": category,
                                                   "target_date": (today() + timedelta(days=days_ahead)).isoformat(),
                                                   **extra})


def test_estimate_with_the_model(client: TestClient, provider: dict, model: MlModel, db_session: Session,
                                 food: dict) -> None:  # noqa: F811
    _seed_logs(db_session, _profile_id(client, provider["headers"]), food["bakery_items"], 40)
    res = _estimate(client, provider["headers"], food["bakery_items"], planned_production=120)
    assert res.status_code == 201, res.text
    e = res.json()
    assert (e["method"], e["confidence"], e["model_version"]) == ("model", "normal", model.version)
    assert Decimal(e["lower_qty"]) <= Decimal(e["predicted_qty"]) <= Decimal(e["upper_qty"])
    assert Decimal(e["predicted_qty"]) % 1 == 0  # packages: whole numbers
    assert "not a guarantee" in e["note"] and "safe" in e["note"] and e["unit"] == "package"
    assert e["food_category_name"] == "Bakery items" and e["planned_production"] == "120.00"


def test_cold_start_rules(client: TestClient, provider: dict, make_user: MakeUser, model: MlModel,
                          db_session: Session, food: dict) -> None:  # noqa: F811
    h = provider["headers"]
    nothing = _estimate(client, h, food["dairy"])
    assert nothing.status_code == 422 and nothing.json()["error"]["code"] == "NO_HISTORY"

    other_user, _ = _make_provider(client, make_user, db_session)
    other = db_session.scalar(select(ProviderDailyLog.provider_id).limit(1))  # none yet
    assert other is None
    other_profile = client.get("/api/v1/providers/me", headers=login_headers(client, other_user.email)).json()["id"]
    _seed_logs(db_session, other_profile, food["dairy"], 20, unit="litre", produced=50, sold=40)
    median = _estimate(client, h, food["dairy"]).json()
    assert (median["method"], median["confidence"], median["unit"]) == ("category_median", "low", "litre")
    assert "no logs for this category" in median["explanation"]

    _seed_logs(db_session, _profile_id(client, h), food["dairy"], 5, unit="litre", produced=30, sold=25)
    own = _estimate(client, h, food["dairy"]).json()
    assert (own["method"], own["confidence"]) == ("own_average", "low")  # too little own history for the model
    assert Decimal(own["predicted_qty"]) == 7 and "limited history" in own["explanation"]  # mean of 5..9


def test_estimate_rules(client: TestClient, provider: dict, make_user: MakeUser, db_session: Session,
                        food: dict) -> None:  # noqa: F811
    h = provider["headers"]
    assert _estimate(client, h, food["bakery_items"], days_ahead=-1).status_code == 422  # the past
    assert _estimate(client, h, food["bakery_items"], days_ahead=8).status_code == 422  # beyond a week
    unverified, _ = _make_provider(client, make_user, db_session, verified=False)
    assert _estimate(client, login_headers(client, unverified.email), food["bakery_items"]).status_code == 403


def test_history_with_actuals(client: TestClient, provider: dict, make_user: MakeUser, model: MlModel,
                              db_session: Session, food: dict) -> None:  # noqa: F811
    h = provider["headers"]
    pid = _profile_id(client, h)
    _seed_logs(db_session, pid, food["bakery_items"], 40)
    made = _estimate(client, h, food["bakery_items"], days_ahead=0).json()
    assert client.get(EST, headers=h).json()["items"][0]["actual_qty"] is None
    client.post(LOGS, headers=h, json={"log_date": today().isoformat(), "food_category_id": food["bakery_items"],
                                       "unit": "package", "produced_qty": 110, "sold_qty": 90})
    item = client.get(EST, headers=h).json()["items"][0]
    assert item["id"] == made["id"] and item["actual_qty"] == "20.00"  # filled in from the log

    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED).email)
    assert client.get(f"{EST}?provider_id={pid}", headers=admin).json()["total"] == 1
    _, other = _make_provider(client, make_user, db_session)
    assert client.get(EST, headers=other).json()["total"] == 0  # only their own
    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER).email)
    assert client.get(EST, headers=volunteer).status_code == 403


# ========================================================== registry and retraining


def test_model_registry(client: TestClient, model: MlModel, make_user: MakeUser, db_session: Session) -> None:
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED).email)
    listed = client.get("/api/v1/admin/ml/models", headers=admin).json()
    assert [m["version"] for m in listed] == [model.version] and listed[0]["data_source"] == "synthetic"
    assert listed[0]["metrics"]["model"]["mae"] < listed[0]["metrics"]["baseline_b2"]["mae"]

    older = MlModel(name="surplus", version="surplus-old", algorithm="Ridge", artifact="surplus-old.joblib",
                    data_source="synthetic", training_rows=1, metrics={})
    db_session.add(older)
    db_session.commit()
    missing = client.post(f"/api/v1/admin/ml/models/{older.id}/activate", headers=admin)
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "MODEL_FILE_MISSING"
    folder = client.app.state.settings.ml_artifacts_dir
    (folder / "surplus-old.joblib").write_bytes((folder / model.artifact).read_bytes())
    rolled = client.post(f"/api/v1/admin/ml/models/{older.id}/activate", headers=admin)
    assert rolled.status_code == 200 and rolled.json()["is_active"] is True
    active = [m["version"] for m in client.get("/api/v1/admin/ml/models", headers=admin).json() if m["is_active"]]
    assert active == ["surplus-old"]  # exactly one active model
    assert db_session.scalar(select(AuditLog.after).where(AuditLog.action == "ml_model.activate")) == {
        "active": "surplus-old"}
    provider = login_headers(client, make_user(role=UserRole.PROVIDER).email)
    assert client.get("/api/v1/admin/ml/models", headers=provider).status_code == 403


def test_new_model_only_replaces_a_better_one(client: TestClient, model: MlModel, db_session: Session,
                                              synthetic, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    folder = client.app.state.settings.ml_artifacts_dir
    real_evaluate = ml_service.evaluate

    def current_is_perfect(bundle, holdout):  # noqa: ANN001, ANN202
        result = real_evaluate(bundle, holdout)
        if bundle is ml_service._cache.get(model.version):
            result["model"]["mae"] = 0.0
        return result
    monkeypatch.setattr(ml_service, "evaluate", current_is_perfect)
    new = ml_service.train_and_register(db_session, folder, synthetic, "synthetic")
    assert new.is_active is False and new.metrics["activation"].startswith("kept inactive: not better than")
    db_session.refresh(model)
    assert model.is_active is True

    monkeypatch.setattr(ml_service, "evaluate", real_evaluate)
    better = ml_service.train_and_register(db_session, folder, synthetic, "synthetic")
    assert better.is_active is True and better.metrics["activation"] == "activated"  # equal or better: goes live
    db_session.refresh(model)
    assert model.is_active is False


def test_a_model_that_loses_to_the_baselines_is_not_activated(client: TestClient, db_session: Session, synthetic,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    real_evaluate = ml_service.evaluate

    def worse_than_b2(bundle, holdout):  # noqa: ANN001, ANN202
        result = real_evaluate(bundle, holdout)
        result["model"]["mae"] = result["baseline_b2"]["mae"] + 1
        return result
    monkeypatch.setattr("app.ml.surplus_training.evaluate", worse_than_b2)
    new = ml_service.train_and_register(db_session, client.app.state.settings.ml_artifacts_dir, synthetic, "synthetic")
    assert new.is_active is False and new.metrics["activation"] == "kept inactive: does not beat the simple baselines"
    assert ml_service.active_model(db_session) is None  # nothing goes live


def test_retrain_job(client: TestClient, provider: dict, db_session: Session, food: dict) -> None:  # noqa: F811
    state = client.app.state

    def run():  # noqa: ANN202
        return run_job(state.engine, state.session_factory, state.storage, "surplus_retrain", "admin",
                       ml_artifacts=state.settings.ml_artifacts_dir)
    thin = run()
    assert thin.status == "success" and thin.result["trained"] is False and "Not enough history" in thin.result["reason"]
    pid = _profile_id(client, provider["headers"])
    _seed_logs(db_session, pid, food["bakery_items"], 330)
    _seed_logs(db_session, pid, food["cooked_meals"], 330, unit="kg", produced=40, sold=33)
    trained = run()
    assert trained.status == "success" and trained.result["trained"] is True, trained.error
    registered = db_session.scalar(select(MlModel).where(MlModel.version == trained.result["version"]))
    assert registered.data_source == "real" and registered.notes == "weekly retraining"
