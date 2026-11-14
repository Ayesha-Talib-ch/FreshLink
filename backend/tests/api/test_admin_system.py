"""Stage 8d: platform settings (each one changes real behaviour), category management and
verification requirements."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.category import FoodCategory
from tests.api.test_allocation import _suggest, scenario  # noqa: F401  (fixture)
from tests.api.test_listings import _create, _make_provider, bread
from tests.api.test_matching import _receiver
from tests.api.test_notifications import food  # noqa: F401  (fixture)
from tests.conftest import MakeUser, login_headers

A = "/api/v1/admin"
SEEDED = ("food_categories", "provider_categories", "receiver_categories", "verification_requirements", "urgency_rules")


@pytest.fixture(autouse=True)
def restore_seeded_tables(test_db_engine: Engine) -> Iterator[None]:
    """Seeded tables survive between tests, so put back exactly what was there before."""
    with test_db_engine.connect() as conn:
        saved = {t: [dict(r._mapping) for r in conn.execute(text(f'SELECT * FROM "{t}"'))] for t in SEEDED}
    yield
    with test_db_engine.begin() as conn:
        for table in ("verification_requirements", "urgency_rules", "food_categories", "provider_categories",
                      "receiver_categories"):
            ids = [r["id"] for r in saved[table]]
            conn.execute(text(f'DELETE FROM "{table}" WHERE NOT (id = ANY(:ids))'), {"ids": ids})
            for row in saved[table]:
                # JSON columns (meals_per_unit) go back as JSON text
                values = {c: (json.dumps(v) if isinstance(v, dict) else v) for c, v in row.items()}
                sets = ", ".join(f'"{c}" = ' + (f"CAST(:{c} AS jsonb)" if isinstance(v, dict) else f":{c}")
                                 for c, v in row.items() if c != "id")
                conn.execute(text(f'UPDATE "{table}" SET {sets} WHERE id = :id'), values)


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=VerificationStatus.APPROVED).email)


def _set(client: TestClient, admin: dict, key: str, value):  # noqa: ANN001, ANN202
    return client.put(f"{A}/settings/{key}", headers=admin, json={"value": value})


# ============================================================== platform settings


def test_settings_list_validate_and_reset(client: TestClient, admin: dict, db_session: Session) -> None:
    listed = {s["key"]: s for s in client.get(f"{A}/settings", headers=admin).json()}
    assert set(listed) == {"matching_weights", "max_travel_km_with_transport", "default_allocation_policy",
                           "match_notify_top_k", "listing_max_days_ahead"}
    assert all(s["is_default"] for s in listed.values()) and listed["match_notify_top_k"]["value"] == 5

    bad_sum = _set(client, admin, "matching_weights", {**listed["matching_weights"]["value"], "distance": 0.9})
    assert bad_sum.status_code == 422 and "add up to 1" in bad_sum.json()["error"]["message"]
    assert _set(client, admin, "matching_weights", {"distance": 1.0}).status_code == 422  # parts missing
    assert _set(client, admin, "match_notify_top_k", 2.5).status_code == 422  # not a whole number
    assert _set(client, admin, "match_notify_top_k", True).status_code == 422
    assert _set(client, admin, "listing_max_days_ahead", 500).status_code == 422
    assert _set(client, admin, "default_allocation_policy", "random").status_code == 422
    assert _set(client, admin, "nonsense", 1).status_code == 404

    changed = _set(client, admin, "match_notify_top_k", 2)
    assert changed.status_code == 200 and changed.json()["value"] == 2 and changed.json()["is_default"] is False
    assert changed.json()["updated_by_email"].endswith("@example.com")
    entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "setting.update"))
    assert (entry.reason, entry.before, entry.after) == ("match_notify_top_k", {"value": 5}, {"value": 2})
    back = client.delete(f"{A}/settings/match_notify_top_k", headers=admin).json()
    assert back["value"] == 5 and back["is_default"] is True


def test_settings_need_an_admin(client: TestClient, make_user: MakeUser) -> None:
    provider = login_headers(client, make_user(role=UserRole.PROVIDER, verification_status=VerificationStatus.APPROVED).email)
    assert client.get(f"{A}/settings", headers=provider).status_code == 403
    assert client.put(f"{A}/settings/match_notify_top_k", headers=provider, json={"value": 1}).status_code == 403


def test_matching_weights_change_the_ranking(client: TestClient, admin: dict, make_user: MakeUser,
                                             db_session: Session, food: dict) -> None:
    _receiver(client, make_user, db_session, food, "Near NGO", lat=31.525, lng=74.355)  # about 0.5 km
    _receiver(client, make_user, db_session, food, "Far but big", lat=31.56, lng=74.36, capacity=30, transport=True)
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food, total_quantity=30))
    url = f"/api/v1/listings/{listing['id']}/matches"
    by_default = [m["receiver"]["receiver_name"] for m in client.get(url, headers=provider).json()["matches"]]
    assert by_default == ["Far but big", "Near NGO"]  # transport + capacity fit win with the default weights

    only_distance = {"distance": 1.0, "capacity_fit": 0, "collection_fit": 0, "urgency_feasibility": 0,
                     "category_priority": 0, "fairness": 0}
    assert _set(client, admin, "matching_weights", only_distance).status_code == 200
    out = client.get(url, headers=provider).json()
    assert [m["receiver"]["receiver_name"] for m in out["matches"]] == ["Near NGO", "Far but big"]
    assert out["weights"] == only_distance  # the response says which weights were used


def test_max_travel_distance(client: TestClient, admin: dict, make_user: MakeUser, db_session: Session,
                             food: dict) -> None:
    _receiver(client, make_user, db_session, food, "Driver NGO", lat=31.75, lng=74.35, transport=True)  # ~25 km
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food))
    url = f"/api/v1/listings/{listing['id']}/matches"
    assert [m["receiver"]["receiver_name"] for m in client.get(url, headers=provider).json()["matches"]] == ["Driver NGO"]
    _set(client, admin, "max_travel_km_with_transport", 10)
    assert client.get(url, headers=provider).json()["matches"] == []  # now too far


def test_default_allocation_policy(client: TestClient, admin: dict, scenario: dict) -> None:  # noqa: F811
    s = scenario
    assert _set(client, admin, "default_allocation_policy", "first_come").status_code == 200
    run = client.post(f"/api/v1/listings/{s['listing']['id']}/allocation-runs", headers=s["provider"], json={})
    assert run.status_code == 201 and run.json()["policy"] == "first_come"
    chosen = _suggest(client, s["provider"], s["listing"]["id"], "proportional_fair")
    assert chosen["policy"] == "proportional_fair"  # an explicit choice still wins


def test_new_listing_notification_count(client: TestClient, admin: dict, make_user: MakeUser,
                                        db_session: Session, food: dict) -> None:
    _, near = _receiver(client, make_user, db_session, food, "Near NGO", lat=31.525, lng=74.355)
    _, provider = _make_provider(client, make_user, db_session)
    _set(client, admin, "match_notify_top_k", 0)
    _create(client, provider, bread(food))
    assert client.get("/api/v1/notifications", headers=near).json()["total"] == 0  # switched off
    client.delete(f"{A}/settings/match_notify_top_k", headers=admin)
    _create(client, provider, bread(food, food_name="Second bread"))
    assert client.get("/api/v1/notifications", headers=near).json()["total"] == 1


def test_listing_days_ahead(client: TestClient, admin: dict, make_user: MakeUser, db_session: Session,
                            food: dict) -> None:
    _, provider = _make_provider(client, make_user, db_session)
    later = datetime.now(UTC) + timedelta(days=3)
    body = bread(food, pickup_window_start=later.isoformat(), pickup_window_end=(later + timedelta(hours=2)).isoformat())
    assert client.post("/api/v1/listings", headers=provider, json=body).status_code == 201
    _set(client, admin, "listing_max_days_ahead", 1)
    refused = client.post("/api/v1/listings", headers=provider, json=body)
    assert refused.status_code == 422 and "at most 1 days ahead" in refused.text


# ======================================================================= categories


def test_food_category_lifecycle(client: TestClient, admin: dict, make_user: MakeUser, db_session: Session) -> None:
    body = {"code": "test_sweets", "name": "  Sweets  and  mithai ", "perishability_class": "bakery",
            "meals_per_unit": {"kg": 5}, "sort_order": 50}
    created = client.post(f"{A}/food-categories", headers=admin, json=body)
    assert created.status_code == 201, created.text
    cat = created.json()
    assert (cat["name"], cat["in_use"], cat["meals_per_unit"]) == ("Sweets and mithai", 0, {"kg": 5.0})
    assert "Sweets and mithai" in [c["name"] for c in client.get("/api/v1/lookups/food-categories").json()]
    rules = client.get(f"{A}/urgency-rules", headers=admin).json()
    assert any(r["food_category"]["id"] == cat["id"] for r in rules)  # a starting urgency rule was added

    assert client.post(f"{A}/food-categories", headers=admin, json=body).status_code == 409  # same code
    assert client.post(f"{A}/food-categories", headers=admin,
                       json={**body, "code": "Bad Code"}).status_code == 422
    update = {k: v for k, v in body.items() if k != "code"} | {"name": "Mithai", "is_active": False}
    hidden = client.put(f"{A}/food-categories/{cat['id']}", headers=admin, json=update)
    assert hidden.status_code == 200 and hidden.json()["code"] == "test_sweets"  # the code never changes
    assert "Mithai" not in [c["name"] for c in client.get("/api/v1/lookups/food-categories").json()]

    _, provider = _make_provider(client, make_user, db_session)
    listing = bread({"bakery_items": cat["id"]})
    assert client.post("/api/v1/listings", headers=provider, json=listing).status_code == 422  # inactive category

    assert client.delete(f"{A}/food-categories/{cat['id']}", headers=admin).status_code == 204
    bakery = db_session.scalar(select(FoodCategory.id).where(FoodCategory.code == "bakery_items"))
    _create(client, provider, bread({"bakery_items": str(bakery)}))
    in_use = client.delete(f"{A}/food-categories/{bakery}", headers=admin)
    assert in_use.status_code == 409 and in_use.json()["error"]["code"] == "CATEGORY_IN_USE"


def test_receiver_and_provider_categories(client: TestClient, admin: dict, make_user: MakeUser) -> None:
    receivers = client.get(f"{A}/receiver-categories", headers=admin).json()
    ngo = next(c for c in receivers if c["code"] == "ngo")
    edit = {"name": ngo["name"], "description": ngo["description"], "priority_weight": 2.5,
            "requires_documents": True, "sort_order": ngo["sort_order"]}
    changed = client.put(f"{A}/receiver-categories/{ngo['id']}", headers=admin, json=edit)
    assert changed.status_code == 200 and changed.json()["priority_weight"] == 2.5
    assert client.put(f"{A}/receiver-categories/{ngo['id']}", headers=admin,
                      json={**edit, "priority_weight": 0}).status_code == 422

    made = client.post(f"{A}/provider-categories", headers=admin, json={"code": "test_hotel", "name": "Hotel"})
    assert made.status_code == 201
    same_name = client.post(f"{A}/provider-categories", headers=admin, json={"code": "test_hotel2", "name": "Hotel"})
    assert same_name.status_code == 409 and same_name.json()["error"]["code"] == "CATEGORY_EXISTS"
    assert client.delete(f"{A}/provider-categories/{made.json()['id']}", headers=admin).status_code == 204
    provider = login_headers(client, make_user(role=UserRole.PROVIDER).email)
    assert client.post(f"{A}/provider-categories", headers=provider,
                       json={"code": "test_x", "name": "X"}).status_code == 403


# ======================================================== verification requirements


def test_verification_requirements(client: TestClient, admin: dict, db_session: Session) -> None:
    receivers = {c["code"]: c["id"] for c in client.get(f"{A}/receiver-categories", headers=admin).json()}
    body = {"role": "receiver", "receiver_category_id": receivers["community_group"],
            "document_type": "test_letter", "name": "Letter from the union council"}
    made = client.post(f"{A}/verification-requirements", headers=admin, json=body)
    assert made.status_code == 201 and made.json()["receiver_category_name"] == "Community group"
    needed = client.get(f"/api/v1/lookups/verification-requirements?role=receiver"
                        f"&receiver_category_id={receivers['community_group']}").json()
    assert "test_letter" in [r["document_type"] for r in needed]
    assert client.post(f"{A}/verification-requirements", headers=admin, json=body).status_code == 409

    off = client.put(f"{A}/verification-requirements/{made.json()['id']}", headers=admin,
                     json={"name": "Letter from the union council", "is_required": False, "is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    needed = client.get(f"/api/v1/lookups/verification-requirements?role=receiver"
                        f"&receiver_category_id={receivers['community_group']}").json()
    assert "test_letter" not in [r["document_type"] for r in needed]

    wrong = client.post(f"{A}/verification-requirements", headers=admin,
                        json={**body, "role": "volunteer", "document_type": "test_other"})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "CATEGORY_ONLY_FOR_RECEIVERS"
    assert len(client.get(f"{A}/verification-requirements", headers=admin).json()) >= 9
