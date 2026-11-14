"""Stage 8b: admin listings monitor, listing moderation, volunteer overview and allocation-run monitor."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.listing import FoodListing
from tests.api.test_allocation import _confirm, _suggest, scenario  # noqa: F401  (fixture)
from tests.api.test_assignments import _volunteer, world  # noqa: F401  (fixture)
from tests.api.test_listings import _create, biryani
from tests.api.test_notifications import _request, food, scene  # noqa: F401  (fixtures)
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
ADMIN = "/api/v1/admin"


@pytest.fixture
def admin(client: TestClient, make_user: MakeUser) -> dict:
    return login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)


def _act(client: TestClient, admin: dict, listing_id: str, action: str, reason: str = "Checked by the admin team"):  # noqa: ANN202
    return client.post(f"{ADMIN}/listings/{listing_id}/{action}", headers=admin, json={"reason": reason})


def _titles(client: TestClient, headers: dict) -> list[str]:
    return [n["title"] for n in client.get("/api/v1/notifications?page_size=100", headers=headers).json()["items"]]


# ================================================================== moderation


def test_suspend_and_reinstate(client: TestClient, admin: dict, scene: dict, db_session: Session) -> None:
    listing, receiver, provider = scene["listing"]["id"], scene["receiver"], scene["provider"]
    pending = _request(client, scene, 5)

    assert client.post(f"{ADMIN}/listings/{listing}/suspend", headers=admin, json={"reason": "no"}).status_code == 422
    done = _act(client, admin, listing, "suspend", "Photos look like another shop")
    assert done.status_code == 200 and done.json()["status"] == "suspended"
    assert done.json()["pending_requests"] == 1  # kept: the listing may come back
    assert client.get("/api/v1/listings", headers=receiver).json()["total"] == 0  # hidden from receivers
    again = client.post("/api/v1/requests", headers=receiver, json={"listing_id": listing, "requested_quantity": 1})
    assert again.status_code == 409
    assert client.post(f"/api/v1/requests/{pending['id']}/approve", headers=provider).status_code == 409
    assert client.post(f"/api/v1/listings/{listing}/resume", headers=provider).status_code == 409  # provider can't lift it
    note = client.get("/api/v1/notifications", headers=provider).json()["items"][0]
    assert note["title"] == "Listing suspended" and note["body"] == "Fresh bread: Photos look like another shop"

    back = _act(client, admin, listing, "reinstate", "The shop sent real photos")
    assert back.status_code == 200 and back.json()["status"] == "active"
    assert client.get("/api/v1/listings", headers=receiver).json()["total"] == 1
    actions = list(db_session.scalars(select(AuditLog.action).where(AuditLog.action.like("listing.%"))))
    assert sorted(actions) == ["listing.reinstate", "listing.suspend"]
    history = client.get(f"/api/v1/listings/{listing}/history", headers=admin).json()
    assert history[-1]["reason"] == "administrator: The shop sent real photos"
    assert _act(client, admin, listing, "reinstate").status_code == 409  # it isn't suspended any more
    assert _act(client, scene["provider"], listing, "suspend").status_code == 403


def test_reinstate_after_the_deadline_is_refused(client: TestClient, admin: dict, scene: dict,
                                                 db_session: Session) -> None:
    listing = scene["listing"]["id"]
    _act(client, admin, listing, "suspend")
    past = datetime.now(UTC) - timedelta(hours=1)
    db_session.execute(update(FoodListing).values(pickup_window_start=past - timedelta(hours=2),
                                                  pickup_window_end=past, collection_deadline=past))
    db_session.commit()
    refused = _act(client, admin, listing, "reinstate")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "DEADLINE_PASSED"
    assert _act(client, admin, listing, "close").json()["status"] == "closed"  # closing is the way out


def test_admin_close_expires_pending_requests(client: TestClient, admin: dict, scene: dict) -> None:
    request = _request(client, scene, 5)
    closed = _act(client, admin, scene["listing"]["id"], "close", "Provider asked us by phone")
    assert closed.status_code == 200 and closed.json()["pending_requests"] == 0
    assert client.get(f"/api/v1/requests/{request['id']}", headers=scene["receiver"]).json()["status"] == "expired"
    assert "Food no longer available" in _titles(client, scene["receiver"])
    assert "Listing closed by an administrator" in _titles(client, scene["provider"])


def test_remove_waits_for_promised_food(client: TestClient, admin: dict, scene: dict) -> None:
    request = _request(client, scene, 5)
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=scene["provider"])
    listing = scene["listing"]["id"]
    refused = _act(client, admin, listing, "remove", "Not food: someone listed a phone")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "LISTING_HAS_ALLOCATIONS"
    assert refused.json()["error"]["details"] == {"undelivered_allocations": 1}

    allocation = client.get(f"/api/v1/requests/{request['id']}", headers=admin).json()["allocation"]["id"]
    client.post(f"/api/v1/allocations/{allocation}/cancel", headers=admin, json={"reason": "Listing is being removed"})
    removed = _act(client, admin, listing, "remove", "Not food: someone listed a phone")
    assert removed.status_code == 200 and removed.json()["status"] == "removed"
    assert _act(client, admin, listing, "close").status_code == 409  # removed is final
    assert client.get(f"/api/v1/listings/{listing}", headers=scene["provider"]).status_code == 404


def test_reinstating_a_fully_allocated_listing(client: TestClient, admin: dict, scene: dict) -> None:
    request = _request(client, scene, 30)
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=scene["provider"])
    listing = scene["listing"]["id"]
    assert _act(client, admin, listing, "suspend").status_code == 200
    assert _act(client, admin, listing, "reinstate").json()["status"] == "fully_allocated"  # nothing left to give


# ============================================================ listings monitor


def test_listings_monitor(client: TestClient, admin: dict, scene: dict, food: dict, db_session: Session) -> None:
    provider = scene["provider"]
    old = _create(client, provider, biryani(food, food_name="Old biryani"))
    _request(client, scene, 4)
    _act(client, admin, old["id"], "close", "Duplicate of another listing")
    db_session.execute(update(FoodListing).where(FoodListing.food_name == "Fresh bread").values(urgency_level="incomplete"))
    db_session.commit()

    def get(query: str = "") -> dict:
        response = client.get(f"{ADMIN}/listings?{query}", headers=admin)
        assert response.status_code == 200, response.text
        return response.json()

    page = get()
    assert page["total"] == 2  # closed ones too, unlike the receiver's browse page
    bread = next(i for i in page["items"] if i["food_name"] == "Fresh bread")
    assert bread["pending_requests"] == 1 and bread["undelivered_allocations"] == 0
    assert bread["date_warnings"] == []  # all dates present, deadline 4 h away
    assert [i["food_name"] for i in get("status=closed")["items"]] == ["Old biryani"]
    assert get("q=bakers")["total"] == 2  # the provider's business name ("Bakers 0001")
    assert get("q=biry")["total"] == 1
    assert [i["food_name"] for i in get("needs_attention=true")["items"]] == ["Fresh bread"]  # incomplete + open
    assert get("deadline_within_hours=5")["total"] == 1  # the bread (pickup ends in 4 h); the closed one isn't open
    assert client.get(f"{ADMIN}/listings", headers=provider).status_code == 403


def test_date_warnings(client: TestClient, admin: dict, scene: dict, db_session: Session) -> None:
    past = datetime.now(UTC) - timedelta(minutes=30)
    db_session.execute(update(FoodListing).values(pickup_window_start=past - timedelta(hours=2),
                                                  pickup_window_end=past, collection_deadline=past))
    db_session.commit()
    item = client.get(f"{ADMIN}/listings", headers=admin).json()["items"][0]
    assert item["status"] == "active"  # the expiry scan hasn't run yet
    assert item["date_warnings"] == ["past its deadline but still open (the expiry scan hasn't run yet)"]


# ================================================================== volunteers


def test_volunteer_overview(client: TestClient, admin: dict, make_user: MakeUser, world: dict) -> None:  # noqa: F811
    _, bilal = _volunteer(client, make_user, "Bilal", vehicle="motorbike")
    _, zara = _volunteer(client, make_user, "Zara")
    zara_id = client.get(f"{ADMIN}/volunteers?q=zara", headers=world["admin"]).json()["items"][0]["id"]
    client.post(f"/api/v1/assignments/{world['task']['id']}/assign", headers=world["admin"],
                json={"volunteer_id": zara_id})
    declined = client.post(f"/api/v1/assignments/{world['task']['id']}/decline", headers=zara,
                           json={"reason": "Exam tomorrow, sorry"})
    assert declined.status_code == 200, declined.text
    task = client.get("/api/v1/assignments/open", headers=bilal).json()[0]["id"]  # the re-opened task
    client.post(f"/api/v1/assignments/{task}/claim", headers=bilal)
    page = client.get(f"{ADMIN}/volunteers", headers=world["admin"]).json()
    assert [v["full_name"] for v in page["items"]] == ["Bilal", "Zara"]
    b = page["items"][0]
    assert (b["active_tasks"], b["completed_deliveries"], b["completion_rate"]) == (1, 0, None)
    assert b["vehicle_type"] == "motorbike" and b["is_available_now"] is True

    for status in ("en_route_pickup",):
        client.post(f"/api/v1/assignments/{task}/status", headers=bilal, json={"status": status})
    client.post(f"/api/v1/assignments/{task}/status", headers=bilal, json={"status": "collected", "quantity": 15})
    client.post(f"/api/v1/assignments/{task}/status", headers=bilal, json={"status": "en_route_dropoff"})
    client.post(f"/api/v1/assignments/{task}/status", headers=bilal, json={"status": "delivered"})
    client.post(f"/api/v1/collections/{world['collection']['id']}/receipt", headers=world["receiver"],
                json={"quantity": 15})
    b = client.get(f"{ADMIN}/volunteers?q=bilal", headers=world["admin"]).json()["items"][0]
    assert (b["active_tasks"], b["completed_deliveries"], b["completion_rate"]) == (0, 1, 1.0)
    z = client.get(f"{ADMIN}/volunteers?q=zara", headers=world["admin"]).json()["items"][0]
    assert (z["declined_tasks"], z["completion_rate"]) == (1, None)  # declining isn't a failed delivery
    assert client.get(f"{ADMIN}/volunteers?available_now=false", headers=world["admin"]).json()["total"] == 0


# ============================================================= allocation runs


def test_allocation_run_monitor(client: TestClient, admin: dict, scenario: dict) -> None:  # noqa: F811
    s = scenario
    first = _suggest(client, s["provider"], s["listing"]["id"])
    run = _suggest(client, s["provider"], s["listing"]["id"], "first_come")  # supersedes the first one
    change = {"request_id": s["requests"]["family"], "approved_quantity": 3, "override_reason": "Asked for less"}
    assert _confirm(client, s["provider"], run["id"], [change]).status_code == 200

    page = client.get(f"{ADMIN}/allocation-runs", headers=admin).json()
    assert page["total"] == 2
    confirmed = next(r for r in page["items"] if r["id"] == run["id"])
    assert confirmed["status"] == "confirmed" and confirmed["overrides"] == 1 and confirmed["requests"] == 3
    assert confirmed["provider_name"].startswith("Bakers") and confirmed["food_name"] == "Fresh bread"
    assert confirmed["approved_total"] < confirmed["suggested_total"]  # the family got 3 instead of the suggestion
    older = next(r for r in page["items"] if r["id"] == first["id"])
    assert older["approved_total"] is None and older["overrides"] == 0
    only = client.get(f"{ADMIN}/allocation-runs?overridden_only=true", headers=admin).json()
    assert [r["id"] for r in only["items"]] == [run["id"]]
    assert client.get(f"{ADMIN}/allocation-runs?policy=first_come", headers=admin).json()["total"] == 1
