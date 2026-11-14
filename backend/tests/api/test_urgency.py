"""Stage 3b: urgency on listings, the expiry scan, and admin urgency rules."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app import cli
from app.core.config import Settings
from app.domain.enums import ListingStatus, UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.category import FoodCategory
from app.models.listing import FoodListing, ListingStatusHistory
from app.services import urgency_service
from tests.api.test_listings import _create, _make_provider, at, biryani, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser, db_session: Session) -> tuple:
    return _make_provider(client, make_user, db_session)


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)


def _move_deadlines(db: Session, listing_id: str, hours: float) -> None:
    """Pretend time has passed: shift the listing's window/deadline to `hours` from now."""
    deadline = datetime.now(UTC) + timedelta(hours=hours)
    db.execute(update(FoodListing).where(FoodListing.id == listing_id).values(
        pickup_window_start=deadline - timedelta(hours=3), pickup_window_end=deadline, collection_deadline=deadline))
    db.commit()


# ================================================================ in responses


def test_listing_response_includes_urgency(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    # Bakery rule (placeholder): medium <= 48 h, high <= 24 h, critical <= 6 h. Deadline in ~4 h -> critical.
    data = _create(client, provider[1], bread(food))
    urgency = data["urgency"]
    assert urgency["level"] == "critical" and urgency["deadline_source"] == "collection deadline"
    assert urgency["explanation"].startswith("Critical urgency: collection deadline in ")
    assert 3.9 < urgency["hours_left"] <= 4  # a few milliseconds under 4 h, shown rounded as "4 h"
    assert urgency["rule"] == {"medium_hours": 48, "high_hours": 24, "critical_hours": 6,
                               "max_hours_since_preparation": 72}
    assert "not a food-safety assessment" in urgency["disclaimer"]
    stored = db_session.scalar(select(FoodListing.urgency_level))
    assert stored == "critical"  # also stored for filtering


def test_cooked_food_uses_the_preparation_time_limit(client: TestClient, provider: tuple, food: dict) -> None:
    # Cooked meals rule: max 12 h after preparation. Prepared 11 h ago -> 1 h left, although the
    # collection deadline is 3 h away.
    data = _create(client, provider[1], biryani(food, preparation_at=at(-11)))
    assert data["urgency"]["deadline_source"] == "preparation time limit (12 h)"
    assert data["urgency"]["level"] == "critical"
    assert 0.9 < data["urgency"]["hours_left"] <= 1


def test_far_deadline_is_normal(client: TestClient, provider: tuple, food: dict) -> None:
    body = bread(food, pickup_window_start=at(60), pickup_window_end=at(72))
    assert _create(client, provider[1], body)["urgency"]["level"] == "normal"


def test_urgency_endpoint_and_lists(client: TestClient, provider: tuple, food: dict) -> None:
    listing = _create(client, provider[1], bread(food))
    urgency = client.get(f"/api/v1/listings/{listing['id']}/urgency", headers=provider[1])
    assert urgency.status_code == 200 and urgency.json()["level"] == "critical"
    items = client.get("/api/v1/providers/me/listings", headers=provider[1]).json()["items"]
    assert items[0]["urgency"]["level"] == "critical"


def test_incomplete_when_category_rules_change(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    """If an admin later makes expiry dates required for bakery items, old listings without
    one show 'incomplete' instead of a misleading 'normal'."""
    listing = _create(client, provider[1], bread(food))
    db_session.execute(text("UPDATE food_categories SET requires_expiry_date = true WHERE code = 'bakery_items'"))
    db_session.commit()
    try:
        data = client.get(f"/api/v1/listings/{listing['id']}", headers=provider[1]).json()
        assert data["urgency"]["level"] == "incomplete"
        assert data["urgency"]["missing_fields"] == ["expiry_at"]
    finally:
        db_session.execute(text("UPDATE food_categories SET requires_expiry_date = false WHERE code = 'bakery_items'"))
        db_session.commit()


def test_receivers_never_see_a_listing_past_its_deadline(
    client: TestClient, provider: tuple, food: dict, make_user: MakeUser, db_session: Session
) -> None:
    listing = _create(client, provider[1], bread(food))
    receiver = login_headers(client, make_user(role=UserRole.RECEIVER, verification_status=APPROVED).email)
    url = f"/api/v1/listings/{listing['id']}"
    assert client.get(url, headers=receiver).status_code == 200

    _move_deadlines(db_session, listing["id"], -0.5)  # deadline passed, scan not run yet
    assert client.get(url, headers=receiver).status_code == 404
    owner_view = client.get(url, headers=provider[1]).json()
    assert owner_view["status"] == "active" and owner_view["urgency"]["is_past_deadline"] is True


# ====================================================================== the scan


def test_scan_expires_overdue_listings(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    overdue = _create(client, provider[1], bread(food, food_name="Overdue"))
    paused_overdue = _create(client, provider[1], bread(food, food_name="Paused overdue"))
    client.post(f"/api/v1/listings/{paused_overdue['id']}/pause", headers=provider[1])
    closed = _create(client, provider[1], bread(food, food_name="Closed"))
    client.post(f"/api/v1/listings/{closed['id']}/close", headers=provider[1])
    fine = _create(client, provider[1], bread(food, food_name="Still fine"))
    for listing in (overdue, paused_overdue, closed):
        _move_deadlines(db_session, listing["id"], -1)

    result = urgency_service.scan(db_session)
    assert result["expired"] == 2 and result["checked"] == 3  # closed listings aren't checked
    assert set(result["expired_ids"]) == {overdue["id"], paused_overdue["id"]}

    db_session.expire_all()
    status = {row.food_name: row.status for row in db_session.scalars(select(FoodListing))}
    assert status == {"Overdue": "expired", "Paused overdue": "expired", "Closed": "closed", "Still fine": "active"}

    entry = db_session.scalar(select(ListingStatusHistory).where(
        ListingStatusHistory.listing_id == overdue["id"], ListingStatusHistory.to_status == "expired"))
    assert entry.from_status == "active" and entry.changed_by is None  # changed by the system
    assert entry.reason.startswith("Deadline passed: the collection deadline was")

    again = urgency_service.scan(db_session)
    assert again["expired"] == 0 and again["checked"] == 1  # idempotent
    assert client.get(f"/api/v1/listings/{fine['id']}", headers=provider[1]).json()["status"] == "active"


def test_expired_listings_are_final(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    listing = _create(client, provider[1], bread(food))
    _move_deadlines(db_session, listing["id"], -1)
    urgency_service.scan(db_session)
    response = client.post(f"/api/v1/listings/{listing['id']}/resume", headers=provider[1])
    assert response.status_code == 409


def test_scan_updates_stored_levels(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    listing = _create(client, provider[1], bread(food, pickup_window_start=at(60), pickup_window_end=at(72)))
    assert db_session.scalar(select(FoodListing.urgency_level)) == "normal"
    _move_deadlines(db_session, listing["id"], 20)  # now within the bakery "high" window (<= 24 h)
    result = urgency_service.scan(db_session)
    assert result["level_changes"] == 1
    db_session.expire_all()
    assert db_session.scalar(select(FoodListing.urgency_level)) == "high"


def test_scan_command(test_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setattr(cli, "get_settings", lambda: test_settings)
    assert cli.main(["urgency-scan"]) == 0
    assert capsys.readouterr().out.startswith("[OK] Checked 0 open listings")


# ================================================================ admin rules


def test_admin_rules_are_admin_only(client: TestClient, provider: tuple) -> None:
    assert client.get("/api/v1/admin/urgency-rules").status_code == 401
    assert client.get("/api/v1/admin/urgency-rules", headers=provider[1]).status_code == 403


def test_admin_lists_and_changes_a_rule(
    client: TestClient, admin: dict, provider: tuple, food: dict, db_session: Session
) -> None:
    rules = client.get("/api/v1/admin/urgency-rules", headers=admin).json()
    assert len(rules) == 7 and rules[0]["food_category"]["code"] == "bakery_items"
    assert "Not a food-safety limit" in rules[0]["note"]

    listing = _create(client, provider[1], bread(food))  # ~4 h left -> critical under the 6 h rule
    url = f"/api/v1/admin/urgency-rules/{food['bakery_items']}"
    original = {"medium_hours": 48, "high_hours": 24, "critical_hours": 6, "max_hours_since_preparation": 72}
    try:
        bad = client.put(url, headers=admin, json={**original, "critical_hours": 30})
        assert bad.status_code == 422 and "critical < high < medium" in bad.json()["error"]["details"]["fields"][0]["message"]

        new = {**original, "critical_hours": 2}  # now 4 h left is only "high"
        saved = client.put(url, headers=admin, json=new)
        assert saved.status_code == 200 and saved.json()["critical_hours"] == 2
        level = client.get(f"/api/v1/listings/{listing['id']}", headers=provider[1]).json()["urgency"]["level"]
        assert level == "high"  # the new rule applies immediately

        log = db_session.scalar(select(AuditLog).where(AuditLog.action == "urgency_rule.update"))
        assert log.before["critical_hours"] == 6 and log.after["critical_hours"] == 2
    finally:
        client.put(url, headers=admin, json=original)  # seeded table is shared between tests

    assert client.put("/api/v1/admin/urgency-rules/00000000-0000-0000-0000-000000000000",
                      headers=admin, json=original).status_code == 404


def test_database_refuses_disordered_thresholds(db_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="ck_urgency_rules_threshold_order"):
        db_session.execute(text("UPDATE urgency_rules SET critical_hours = 100"))
        db_session.commit()
    db_session.rollback()


def test_status_values_unchanged() -> None:
    assert ListingStatus.EXPIRED.value == "expired"
