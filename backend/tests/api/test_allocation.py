"""Stage 5a: allocation runs, confirm (with overrides), approve shortcut, cancel.

The main scenario is the specification's own example (spec 5.5): a bakery has 30 bread
packages; an NGO asks for 15, a community kitchen for 10 and a family for 10.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import UserRole, VerificationStatus
from app.models.allocation import AllocationRun
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ReceiverCategory
from app.models.listing import FoodListing
from app.models.profiles import ReceiverProfile
from app.models.user import User
from app.services import allocation_service
from app.services.audit_service import ClientInfo
from tests.api.test_listings import _create, _make_provider, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


def _receiver(client: TestClient, make_user: MakeUser, db: Session, code: str, name: str) -> tuple[User, dict]:
    user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED, full_name=f"{name} contact")
    headers = login_headers(client, user.email)
    category = db.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == code))
    body = {"receiver_name": name, "receiver_category_id": str(category), "address": "Model Town, Lahore",
            "latitude": 31.48, "longitude": 74.32}
    if code == "individual":
        body["household_size"] = 6
    assert client.put("/api/v1/receivers/me", headers=headers, json=body).status_code == 200
    return user, headers


@pytest.fixture
def scenario(client: TestClient, make_user: MakeUser, db_session: Session, food: dict) -> dict:
    """Spec 5.5: 30 packages; NGO 15, community kitchen 10, family 10 (requested in that order)."""
    provider_user, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food, total_quantity=30))
    people = {"ngo": _receiver(client, make_user, db_session, "ngo", "Hope NGO"),
              "kitchen": _receiver(client, make_user, db_session, "community_kitchen", "Model Town Kitchen"),
              "family": _receiver(client, make_user, db_session, "individual", "Raza Family")}
    requests = {}
    for key, quantity in (("ngo", 15), ("kitchen", 10), ("family", 10)):
        response = client.post("/api/v1/requests", headers=people[key][1],
                               json={"listing_id": listing["id"], "requested_quantity": quantity})
        assert response.status_code == 201, response.text
        requests[key] = response.json()["id"]
        time.sleep(0.01)  # distinct request times
    return {"provider": provider, "provider_user": provider_user, "listing": listing, "people": people,
            "requests": requests}


def _suggest(client: TestClient, headers: dict, listing_id: str, policy: str = "priority_weighted") -> dict:
    response = client.post(f"/api/v1/listings/{listing_id}/allocation-runs", headers=headers, json={"policy": policy})
    assert response.status_code == 201, response.text
    return response.json()


def _by_request(run: dict, s: dict) -> dict[str, float]:
    names = {v: k for k, v in s["requests"].items()}
    return {names[line["request_id"]]: line["suggested_quantity"] for line in run["lines"]}


def _confirm(client: TestClient, headers: dict, run_id: str, lines: list | None = None):  # noqa: ANN202
    return client.post(f"/api/v1/allocation-runs/{run_id}/confirm", headers=headers, json={"lines": lines or []})


# ==================================================================== the spec example


def test_spec_example_end_to_end(client: TestClient, scenario: dict, db_session: Session) -> None:
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    assert run["status"] == "draft" and run["available_snapshot"] == 30
    assert _by_request(run, s) == {"ngo": 15, "kitchen": 10, "family": 5}  # exactly as in spec 5.5
    family_line = next(line for line in run["lines"] if line["request_id"] == s["requests"]["family"])
    assert family_line["receiver_name"] == "Raza Family" and family_line["priority_weight"] == 0.7
    assert family_line["explanation"].startswith("5 packages of 10 packages requested: the remaining quantity")

    confirmed = _confirm(client, s["provider"], run["id"])
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"

    listing = client.get(f"/api/v1/listings/{s['listing']['id']}", headers=s["provider"]).json()
    assert listing["allocated_quantity"] == 30 and listing["remaining_quantity"] == 0
    assert listing["status"] == "fully_allocated"

    statuses = {k: client.get(f"/api/v1/requests/{rid}", headers=s["provider"]).json() for k, rid in s["requests"].items()}
    assert {k: r["status"] for k, r in statuses.items()} == {
        "ngo": "approved", "kitchen": "approved", "family": "partially_approved"}

    family = client.get(f"/api/v1/requests/{s['requests']['family']}", headers=s["people"]["family"][1]).json()
    assert family["allocation"]["approved_quantity"] == 5  # the receiver sees the result
    allocations = client.get("/api/v1/allocations", headers=s["people"]["family"][1]).json()
    assert allocations["total"] == 1 and allocations["items"][0]["approved_quantity"] == 5
    assert allocations["items"][0]["listing"]["food_name"] == "Fresh bread"

    # Spec 5.5 records: available, requested, recommended, approved, remaining
    stored = db_session.get(AllocationRun, run["id"])
    assert stored.input_snapshot["listing"]["total"] == "30.00"
    assert len(stored.input_snapshot["candidates"]) == 3


def test_proportional_fair_policy(client: TestClient, scenario: dict) -> None:
    run = _suggest(client, scenario["provider"], scenario["listing"]["id"], "proportional_fair")
    assert _by_request(run, scenario) == {"ngo": 13, "kitchen": 9, "family": 8}


def test_fully_allocated_food_disappears_from_browse(client: TestClient, scenario: dict) -> None:
    s = scenario
    _confirm(client, s["provider"], _suggest(client, s["provider"], s["listing"]["id"])["id"])
    other = s["people"]["ngo"][1]
    assert client.get("/api/v1/listings", headers=other, params={"radius_km": 50}).json()["total"] == 0


# ========================================================================= permissions


def test_who_may_allocate(client: TestClient, scenario: dict, make_user: MakeUser, db_session: Session) -> None:
    url = f"/api/v1/listings/{scenario['listing']['id']}/allocation-runs"
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, headers=scenario["people"]["ngo"][1], json={}).status_code == 403
    _, other_provider = _make_provider(client, make_user, db_session)
    assert client.post(url, headers=other_provider, json={}).status_code == 404

    admin_user = make_user(role=UserRole.ADMIN, verification_status=APPROVED)
    admin = login_headers(client, admin_user.email)
    run = _suggest(client, admin, scenario["listing"]["id"])
    assert _confirm(client, admin, run["id"]).status_code == 200
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "allocation.confirm"))
    assert log.actor_id == admin_user.id  # administrator decisions are audited


def test_no_pending_requests(client: TestClient, make_user: MakeUser, db_session: Session, food: dict) -> None:
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food))
    response = client.post(f"/api/v1/listings/{listing['id']}/allocation-runs", headers=provider, json={})
    assert response.status_code == 409 and _code(response) == "NO_PENDING_REQUESTS"


# =========================================================================== overrides


def test_override_needs_a_reason_and_is_audited(client: TestClient, scenario: dict, db_session: Session) -> None:
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    change = {"request_id": s["requests"]["family"], "approved_quantity": 3}
    no_reason = _confirm(client, s["provider"], run["id"], [change])
    assert no_reason.status_code == 422 and _code(no_reason) == "OVERRIDE_REASON_REQUIRED"

    ok = _confirm(client, s["provider"], run["id"], [{**change, "override_reason": "Family asked for less today"}])
    assert ok.status_code == 200
    listing = client.get(f"/api/v1/listings/{s['listing']['id']}", headers=s["provider"]).json()
    assert listing["remaining_quantity"] == 2 and listing["status"] == "active"  # 2 left, still listed
    family = client.get(f"/api/v1/requests/{s['requests']['family']}", headers=s["provider"]).json()
    assert family["allocation"]["approved_quantity"] == 3
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "allocation.confirm"))
    assert log.reason == "Family asked for less today"
    assert log.after["overrides"][0]["suggested"] == "5.00" and log.after["overrides"][0]["approved"] == "3"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ([("family", 10, "Give the family everything")], "EXCEEDS_AVAILABLE"),  # 15 + 10 + 10 > 30
        ([("ngo", 16, "More for the NGO")], "INVALID_APPROVED_QUANTITY"),  # more than requested
        ([("ngo", 14.5, "Half a package")], "WHOLE_NUMBER_REQUIRED"),
        ([("ngo", 0, "No"), ("kitchen", 0, "No"), ("family", 0, "No")], "NOTHING_APPROVED"),
    ],
)
def test_invalid_confirmations(client: TestClient, scenario: dict, changes: list, code: str) -> None:
    run = _suggest(client, scenario["provider"], scenario["listing"]["id"])
    lines = [{"request_id": scenario["requests"][who], "approved_quantity": q, "override_reason": reason + " ok"}
             for who, q, reason in changes]
    response = _confirm(client, scenario["provider"], run["id"], lines)
    assert response.status_code == 422 and _code(response) == code
    listing = client.get(f"/api/v1/listings/{scenario['listing']['id']}", headers=scenario["provider"]).json()
    assert listing["allocated_quantity"] == 0  # nothing was saved


def test_unknown_request_in_changes(client: TestClient, scenario: dict) -> None:
    run = _suggest(client, scenario["provider"], scenario["listing"]["id"])
    response = _confirm(client, scenario["provider"], run["id"], [
        {"request_id": "00000000-0000-0000-0000-000000000000", "approved_quantity": 1, "override_reason": "who?"}])
    assert response.status_code == 422 and _code(response) == "UNKNOWN_REQUEST"


def test_a_zero_line_rejects_that_request_with_the_explanation(
    client: TestClient, make_user: MakeUser, db_session: Session, food: dict
) -> None:
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food, total_quantity=25))
    ids = []
    for code, name, quantity in (("ngo", "First", 15), ("ngo", "Second", 10), ("ngo", "Third", 10)):
        _, headers = _receiver(client, make_user, db_session, code, name)
        ids.append(client.post("/api/v1/requests", headers=headers,
                               json={"listing_id": listing["id"], "requested_quantity": quantity}).json()["id"])
        time.sleep(0.01)
    run = _suggest(client, provider, listing["id"], "first_come")
    assert [line["suggested_quantity"] for line in run["lines"]] == [15, 10, 0]
    _confirm(client, provider, run["id"])
    third = client.get(f"/api/v1/requests/{ids[2]}", headers=provider).json()
    assert third["status"] == "rejected"
    assert third["rejection_reason"] == "Not allocated: nothing left after earlier requests."


# ======================================================================== staleness


def test_listing_change_makes_a_suggestion_stale(client: TestClient, scenario: dict, food: dict) -> None:
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    edited = client.put(f"/api/v1/listings/{s['listing']['id']}", headers={**s["provider"], "If-Match": "1"},
                        json=bread(food, total_quantity=40))
    assert edited.status_code == 200
    response = _confirm(client, s["provider"], run["id"])
    assert response.status_code == 409 and _code(response) == "RUN_STALE"
    assert client.get(f"/api/v1/allocation-runs/{run['id']}", headers=s["provider"]).json()["status"] == "superseded"


def test_cancelled_request_makes_a_suggestion_stale(client: TestClient, scenario: dict) -> None:
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    client.post(f"/api/v1/requests/{s['requests']['kitchen']}/cancel", headers=s["people"]["kitchen"][1])
    response = _confirm(client, s["provider"], run["id"])
    assert response.status_code == 409 and _code(response) == "RUN_STALE"


def test_only_the_newest_suggestion_can_be_confirmed(client: TestClient, scenario: dict) -> None:
    s = scenario
    old = _suggest(client, s["provider"], s["listing"]["id"])
    new = _suggest(client, s["provider"], s["listing"]["id"], "proportional_fair")
    response = _confirm(client, s["provider"], old["id"])
    assert response.status_code == 409 and _code(response) == "RUN_NOT_DRAFT"
    assert _confirm(client, s["provider"], new["id"]).status_code == 200
    again = _confirm(client, s["provider"], new["id"])
    assert again.status_code == 409 and _code(again) == "RUN_NOT_DRAFT"
    runs = client.get(f"/api/v1/listings/{s['listing']['id']}/allocation-runs", headers=s["provider"]).json()
    assert [r["status"] for r in runs] == ["confirmed", "superseded"]


def test_discard(client: TestClient, scenario: dict) -> None:
    run = _suggest(client, scenario["provider"], scenario["listing"]["id"])
    discarded = client.post(f"/api/v1/allocation-runs/{run['id']}/discard", headers=scenario["provider"])
    assert discarded.json()["status"] == "discarded"
    assert _confirm(client, scenario["provider"], run["id"]).status_code == 409


def test_two_confirms_at_once_only_one_wins(
    client: TestClient, scenario: dict, test_db_engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    original = allocation_service._ensure_open

    def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        time.sleep(0.5)
        return original(*args, **kwargs)

    monkeypatch.setattr(allocation_service, "_ensure_open", slow)
    barrier, results = threading.Barrier(2), []

    def attempt() -> None:
        with Session(test_db_engine, expire_on_commit=False) as db:
            user = db.get(User, s["provider_user"].id)
            barrier.wait()
            try:
                allocation_service.confirm(db, user, run["id"], {}, ClientInfo(None, None))
                results.append("ok")
            except AppError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == ["RUN_NOT_DRAFT", "ok"]
    with Session(test_db_engine) as db:
        assert db.scalar(select(FoodListing.allocated_quantity)) == 30  # counted once, not twice


# ======================================================= eligibility and capacity


def test_ineligible_receivers_get_nothing(client: TestClient, scenario: dict, db_session: Session) -> None:
    s = scenario
    db_session.execute(update(ReceiverProfile).where(ReceiverProfile.user_id == s["people"]["kitchen"][0].id)
                       .values(is_eligible=False))
    db_session.commit()
    run = _suggest(client, s["provider"], s["listing"]["id"])
    assert _by_request(run, s) == {"ngo": 15, "kitchen": 0, "family": 10}
    kitchen_line = next(line for line in run["lines"] if line["request_id"] == s["requests"]["kitchen"])
    assert kitchen_line["explanation"] == "Not allocated: the receiver is currently not eligible."
    forced = _confirm(client, s["provider"], run["id"], [
        {"request_id": s["requests"]["kitchen"], "approved_quantity": 5, "override_reason": "Give them some"}])
    assert forced.status_code == 422 and _code(forced) == "INELIGIBLE_RECEIVER"


def test_suggestion_respects_capacity(client: TestClient, scenario: dict, food: dict) -> None:
    s = scenario
    # The NGO now declares it can take at most 12 packages of bakery items per pickup.
    client.put("/api/v1/receivers/me/food-preferences", headers=s["people"]["ngo"][1], json={"preferences": [
        {"food_category_id": food["bakery_items"], "max_quantity_per_pickup": 12, "unit": "package"}]})
    run = _suggest(client, s["provider"], s["listing"]["id"])
    assert _by_request(run, s) == {"ngo": 12, "kitchen": 10, "family": 8}


# ===================================================================== approve shortcut


def test_approve_one_request(client: TestClient, scenario: dict) -> None:
    s = scenario
    full = client.post(f"/api/v1/requests/{s['requests']['kitchen']}/approve", headers=s["provider"])
    assert full.status_code == 200 and full.json()["approved_quantity"] == 10
    assert client.get(f"/api/v1/requests/{s['requests']['kitchen']}", headers=s["provider"]).json()["status"] == "approved"

    url = f"/api/v1/requests/{s['requests']['ngo']}/approve"
    no_reason = client.post(url, headers=s["provider"], json={"approved_quantity": 8})
    assert no_reason.status_code == 422 and _code(no_reason) == "OVERRIDE_REASON_REQUIRED"
    partial = client.post(url, headers=s["provider"],
                          json={"approved_quantity": 8, "override_reason": "Keeping some for the family"})
    assert partial.status_code == 200
    assert client.get(f"/api/v1/requests/{s['requests']['ngo']}", headers=s["provider"]).json()["status"] == "partially_approved"

    twice = client.post(f"/api/v1/requests/{s['requests']['kitchen']}/approve", headers=s["provider"])
    assert twice.status_code == 409


# ============================================================================== cancel


def test_cancel_allocation_returns_the_food(client: TestClient, scenario: dict, db_session: Session) -> None:
    s = scenario
    _confirm(client, s["provider"], _suggest(client, s["provider"], s["listing"]["id"])["id"])
    family_allocation = client.get(f"/api/v1/requests/{s['requests']['family']}",
                                   headers=s["provider"]).json()["allocation"]["id"]
    url = f"/api/v1/allocations/{family_allocation}/cancel"
    assert client.post(url, headers=s["provider"], json={"reason": "no"}).status_code == 422
    assert client.post(url, headers=s["people"]["family"][1], json={"reason": "I cancel it"}).status_code == 403

    response = client.post(url, headers=s["provider"], json={"reason": "Family can't come today"})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    listing = client.get(f"/api/v1/listings/{s['listing']['id']}", headers=s["provider"]).json()
    assert listing["allocated_quantity"] == 25 and listing["status"] == "active"  # back on offer
    family = client.get(f"/api/v1/requests/{s['requests']['family']}", headers=s["provider"]).json()
    assert family["status"] == "cancelled"
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "allocation.cancel")).reason == \
        "Family can't come today"

    again = client.post(url, headers=s["provider"], json={"reason": "Once more"})
    assert again.status_code == 409 and _code(again) == "ALLOCATION_NOT_CANCELLABLE"


# =========================================================================== reading


def test_each_role_sees_only_its_own_allocations(client: TestClient, scenario: dict, make_user: MakeUser,
                                                 db_session: Session) -> None:
    s = scenario
    _confirm(client, s["provider"], _suggest(client, s["provider"], s["listing"]["id"])["id"])
    assert client.get("/api/v1/allocations", headers=s["provider"]).json()["total"] == 3
    assert client.get("/api/v1/allocations", headers=s["people"]["ngo"][1]).json()["total"] == 1
    _, other_provider = _make_provider(client, make_user, db_session)
    assert client.get("/api/v1/allocations", headers=other_provider).json()["total"] == 0
    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)
    assert client.get("/api/v1/allocations", headers=volunteer).status_code == 403

    ngo_allocation = client.get("/api/v1/allocations", headers=s["people"]["ngo"][1]).json()["items"][0]["id"]
    assert client.get(f"/api/v1/allocations/{ngo_allocation}", headers=s["people"]["family"][1]).status_code == 404
    assert client.get(f"/api/v1/allocations/{ngo_allocation}", headers=s["provider"]).status_code == 200


def test_database_is_the_last_line_of_defence(client: TestClient, scenario: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the application's own total check had a bug, the database CHECK
    (allocated <= total) refuses over-allocation, and the user gets a clear 409."""
    s = scenario
    run = _suggest(client, s["provider"], s["listing"]["id"])
    # Simulate the bug: switch off the application's "not more than available" check.
    monkeypatch.setattr(allocation_service, "_ensure_within_available", lambda total, listing: None)
    lines = [{"request_id": s["requests"]["family"], "approved_quantity": 10, "override_reason": "Simulated bug"}]
    response = _confirm(client, s["provider"], run["id"], lines)  # 15 + 10 + 10 = 35 > 30
    assert response.status_code == 409 and _code(response) == "ALLOCATION_CONFLICT"
    listing = client.get(f"/api/v1/listings/{s['listing']['id']}", headers=s["provider"]).json()
    assert listing["allocated_quantity"] == 0  # nothing was saved
