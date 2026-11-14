"""Stage 6b: volunteer assignments - open tasks, claim, assign/accept/decline,
delivery steps, privacy, admin suggestions/reassign/cancel."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ReceiverCategory
from app.models.profiles import VolunteerProfile
from app.models.user import User
from tests.api.test_listings import _create, _make_provider, at, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
GULBERG = {"latitude": 31.52, "longitude": 74.36}


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


def _volunteer(client: TestClient, make_user: MakeUser, name: str, *, lat: float = 31.515, lng: float = 74.355,
               radius: float = 10, vehicle: str | None = None, verified: bool = True) -> tuple[User, dict]:
    user = make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED if verified else VerificationStatus.PENDING,
                     full_name=name)
    headers = login_headers(client, user.email)
    body = {"address": f"{name} home", "latitude": lat, "longitude": lng, "service_radius_km": radius,
            "has_vehicle": vehicle is not None, "vehicle_type": vehicle, "is_available_now": True}
    assert client.put("/api/v1/volunteers/me", headers=headers, json=body).status_code == 200
    return user, headers


@pytest.fixture
def world(client: TestClient, make_user: MakeUser, db_session: Session, food: dict) -> dict:
    """A bakery in Gulberg; an NGO in Model Town requests 15 packages and NEEDS A VOLUNTEER.
    Approval creates a collection with an open delivery task."""
    receiver_user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED, full_name="Sara (NGO)")
    receiver = login_headers(client, receiver_user.email)
    category = db_session.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == "ngo"))
    client.put("/api/v1/receivers/me", headers=receiver, json={
        "receiver_name": "Hope NGO", "receiver_category_id": str(category), "address": "House 9, Model Town",
        "latitude": 31.48, "longitude": 74.32})
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food, total_quantity=30))
    request = client.post("/api/v1/requests", headers=receiver, json={
        "listing_id": listing["id"], "requested_quantity": 15, "collection_preference": "needs_volunteer",
        "preferred_pickup_at": at(2)}).json()
    assert client.post(f"/api/v1/requests/{request['id']}/approve", headers=provider).status_code == 200
    collection = client.get("/api/v1/collections", headers=provider).json()["items"][0]
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    task = client.get("/api/v1/assignments", headers=admin).json()["items"][0]
    return {"provider": provider, "receiver": receiver, "admin": admin, "listing": listing,
            "collection": collection, "task": task, "request": request}


def task_url(w: dict, action: str = "") -> str:
    return f"/api/v1/assignments/{w['task']['id']}" + (f"/{action}" if action else "")


def _step(client: TestClient, headers: dict, w: dict, status: str, **extra) -> object:  # noqa: ANN003
    return client.post(task_url(w, "status"), headers=headers, json={"status": status, **extra})


# ====================================================================== open tasks


def test_volunteer_collection_opens_a_task(world: dict) -> None:
    assert world["collection"]["method"] == "volunteer"
    task = world["task"]
    assert task["status"] == "open" and task["quantity"] == 15 and task["food_name"] == "Fresh bread"
    assert 5 < task["delivery_distance_km"] < 7


def test_open_tasks_are_near_and_hide_private_details(client: TestClient, world: dict, make_user: MakeUser) -> None:
    _, near = _volunteer(client, make_user, "Ali")
    tasks = client.get("/api/v1/assignments/open", headers=near).json()
    assert len(tasks) == 1 and tasks[0]["distance_km"] < 2
    assert tasks[0]["pickup_address"]  # the business pickup point is shown
    assert tasks[0]["dropoff_address"] is None  # the receiver's address is NOT, until accepted
    assert tasks[0]["provider_contact"] is None and tasks[0]["receiver_contact"] is None

    _, far = _volunteer(client, make_user, "Far away", lat=31.25, lng=74.21, radius=5)
    assert client.get("/api/v1/assignments/open", headers=far).json() == []


def test_who_may_see_open_tasks(client: TestClient, world: dict, make_user: MakeUser) -> None:
    assert client.get("/api/v1/assignments/open").status_code == 401
    assert client.get("/api/v1/assignments/open", headers=world["receiver"]).status_code == 403
    _, unverified = _volunteer(client, make_user, "New", verified=False)
    response = client.get("/api/v1/assignments/open", headers=unverified)
    assert response.status_code == 403 and _code(response) == "VERIFICATION_REQUIRED"
    no_profile = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)
    assert client.get("/api/v1/assignments/open", headers=no_profile).status_code == 409


# =================================================================== the journey


def test_claim_and_deliver(client: TestClient, world: dict, make_user: MakeUser, db_session: Session) -> None:
    _, ali = _volunteer(client, make_user, "Ali", vehicle="motorbike")
    claimed = client.post(task_url(world, "claim"), headers=ali)
    assert claimed.status_code == 200 and claimed.json()["status"] == "accepted"
    data = claimed.json()
    assert data["dropoff_address"] == "House 9, Model Town"  # visible now
    assert data["receiver_contact"]["contact_phone"].startswith("+92")
    assert data["provider_contact"]["contact_person"] == "Ali Raza"

    _, bilal = _volunteer(client, make_user, "Bilal")
    taken = client.post(task_url(world, "claim"), headers=bilal)
    assert taken.status_code == 409 and _code(taken) == "TASK_NOT_OPEN"

    assert _step(client, ali, world, "en_route_pickup").json()["status"] == "en_route_pickup"
    no_quantity = _step(client, ali, world, "collected")
    assert no_quantity.status_code == 422 and _code(no_quantity) == "QUANTITY_REQUIRED"
    collected = _step(client, ali, world, "collected", quantity=15, note="3 crates")
    assert collected.status_code == 200 and collected.json()["handed_over_quantity"] == 15
    collection = client.get(f"/api/v1/collections/{world['collection']['id']}", headers=world["provider"]).json()
    assert collection["status"] == "handed_over"  # the volunteer's step IS the handover

    assert _step(client, ali, world, "en_route_dropoff").status_code == 200
    assert _step(client, ali, world, "delivered").json()["status"] == "delivered"

    # The receiver confirms what arrived: delivery completed.
    receipt = client.post(f"/api/v1/collections/{world['collection']['id']}/receipt", headers=world["receiver"],
                          json={"quantity": 15})
    assert receipt.status_code == 200
    final = client.get(task_url(world), headers=ali).json()
    assert final["status"] == "completed" and final["completed_at"] is not None
    assert final["receiver_contact"] is not None  # still visible for 7 days after completion
    history = [h["to_status"] for h in client.get(task_url(world, "history"), headers=ali).json()]
    assert history == ["open", "accepted", "en_route_pickup", "collected", "en_route_dropoff", "delivered",
                       "completed"]


def test_steps_must_follow_the_order(client: TestClient, world: dict, make_user: MakeUser) -> None:
    _, ali = _volunteer(client, make_user, "Ali")
    client.post(task_url(world, "claim"), headers=ali)
    skipped = _step(client, ali, world, "delivered")  # can't deliver before collecting
    assert skipped.status_code == 409 and _code(skipped) == "INVALID_STATUS_TRANSITION"
    _, other = _volunteer(client, make_user, "Other")
    assert _step(client, other, world, "en_route_pickup").status_code == 404  # not their task


def test_provider_handover_moves_the_task_too(client: TestClient, world: dict, make_user: MakeUser) -> None:
    _, ali = _volunteer(client, make_user, "Ali")
    client.post(task_url(world, "claim"), headers=ali)
    client.post(f"/api/v1/collections/{world['collection']['id']}/handover", headers=world["provider"],
                json={"quantity": 15})
    assert client.get(task_url(world), headers=ali).json()["status"] == "collected"


def test_volunteer_failure_before_pickup_returns_the_food(client: TestClient, world: dict, make_user: MakeUser) -> None:
    _, ali = _volunteer(client, make_user, "Ali")
    client.post(task_url(world, "claim"), headers=ali)
    no_reason = _step(client, ali, world, "failed", note="no")
    assert no_reason.status_code == 422 and _code(no_reason) == "REASON_REQUIRED"
    failed = _step(client, ali, world, "failed", note="Bike broke down on the way")
    assert failed.status_code == 200 and failed.json()["failure_reason"] == "Bike broke down on the way"
    collection = client.get(f"/api/v1/collections/{world['collection']['id']}", headers=world["provider"]).json()
    assert collection["status"] == "failed"
    listing = client.get(f"/api/v1/listings/{world['listing']['id']}", headers=world["provider"]).json()
    assert listing["remaining_quantity"] == 30  # the 15 never left the bakery


# ============================================================ assign / accept / decline


def test_admin_suggestions_and_assignment(client: TestClient, world: dict, make_user: MakeUser,
                                          db_session: Session) -> None:
    near_user, near = _volunteer(client, make_user, "Near with van", vehicle="van")
    _volunteer(client, make_user, "Further", lat=31.45, lng=74.30)
    _volunteer(client, make_user, "Too far", lat=31.25, lng=74.21, radius=5)
    data = client.get(task_url(world, "suggested-volunteers"), headers=world["admin"]).json()
    assert data["suggestions"][0]["volunteer"]["full_name"] == "Near with van"
    assert "has a van" in data["suggestions"][0]["reasons"]
    assert [e["volunteer"]["full_name"] for e in data["excluded"]] == ["Too far"]
    assert client.get(task_url(world, "suggested-volunteers"), headers=near).status_code == 403

    volunteer_id = data["suggestions"][0]["volunteer"]["id"]
    assigned = client.post(task_url(world, "assign"), headers=world["admin"], json={"volunteer_id": volunteer_id})
    assert assigned.status_code == 200 and assigned.json()["status"] == "assigned"
    assert assigned.json()["volunteer"]["full_name"] == "Near with van"
    assert db_session.scalar(select(AuditLog.action).where(AuditLog.action == "assignment.assign")) is not None

    seen = client.get(task_url(world), headers=near).json()
    assert seen["dropoff_address"] is None  # assigned but not yet accepted: still private
    accepted = client.post(task_url(world, "accept"), headers=near)
    assert accepted.json()["status"] == "accepted" and accepted.json()["dropoff_address"] is not None


def test_decline_reopens_the_task(client: TestClient, world: dict, make_user: MakeUser, db_session: Session) -> None:
    _, near = _volunteer(client, make_user, "Busy person")
    profile_id = db_session.scalar(select(VolunteerProfile.id))
    client.post(task_url(world, "assign"), headers=world["admin"], json={"volunteer_id": str(profile_id)})
    assert client.post(task_url(world, "decline"), headers=near, json={"reason": "no"}).status_code == 422
    declined = client.post(task_url(world, "decline"), headers=near, json={"reason": "Exam tomorrow, sorry"})
    assert declined.status_code == 200 and declined.json()["status"] == "declined"
    tasks = client.get("/api/v1/assignments", headers=world["admin"]).json()["items"]
    assert sorted(t["status"] for t in tasks) == ["declined", "open"]  # offered to others again


def test_reassign_before_start(client: TestClient, world: dict, make_user: MakeUser, db_session: Session) -> None:
    first_user, first = _volunteer(client, make_user, "First")
    second_user, second = _volunteer(client, make_user, "Second")
    client.post(task_url(world, "claim"), headers=first)
    second_profile = db_session.scalar(select(VolunteerProfile.id).where(VolunteerProfile.user_id == second_user.id))
    moved = client.post(task_url(world, "assign"), headers=world["admin"], json={"volunteer_id": str(second_profile)})
    assert moved.status_code == 200 and moved.json()["volunteer"]["full_name"] == "Second"
    assert moved.json()["id"] != world["task"]["id"]  # a new task; the old one is cancelled
    assert client.get(task_url(world), headers=world["admin"]).json()["status"] == "cancelled"

    started = client.post(f"/api/v1/assignments/{moved.json()['id']}/accept", headers=second)
    assert started.status_code == 200
    client.post(f"/api/v1/assignments/{moved.json()['id']}/status", headers=second, json={"status": "collected",
                                                                                           "quantity": 15})
    late = client.post(f"/api/v1/assignments/{moved.json()['id']}/assign", headers=world["admin"],
                       json={"volunteer_id": str(second_profile)})
    assert late.status_code == 409 and _code(late) == "TASK_ALREADY_STARTED"


def test_admin_cancel_makes_it_a_self_pickup(client: TestClient, world: dict) -> None:
    cancelled = client.post(task_url(world, "cancel"), headers=world["admin"], json={"reason": "Receiver will come"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    collection = client.get(f"/api/v1/collections/{world['collection']['id']}", headers=world["provider"]).json()
    assert collection["method"] == "self_pickup"


def test_switching_methods(client: TestClient, world: dict) -> None:
    url = f"/api/v1/collections/{world['collection']['id']}"
    client.patch(url, headers=world["receiver"], json={"method": "self_pickup"})
    assert client.get(task_url(world), headers=world["admin"]).json()["status"] == "cancelled"
    client.patch(url, headers=world["receiver"], json={"method": "volunteer"})
    statuses = sorted(t["status"] for t in client.get("/api/v1/assignments", headers=world["admin"]).json()["items"])
    assert statuses == ["cancelled", "open"]


# ======================================================================= scoping


def test_each_role_sees_only_its_own_tasks(client: TestClient, world: dict, make_user: MakeUser) -> None:
    _, ali = _volunteer(client, make_user, "Ali")
    _, other = _volunteer(client, make_user, "Other")
    client.post(task_url(world, "claim"), headers=ali)
    assert client.get("/api/v1/assignments", headers=ali).json()["total"] == 1
    assert client.get("/api/v1/assignments", headers=other).json()["total"] == 0
    assert client.get(task_url(world), headers=other).status_code == 404  # taken, not theirs
    assert client.get("/api/v1/assignments", headers=world["provider"]).json()["total"] == 1
    receiver_view = client.get(task_url(world), headers=world["receiver"]).json()
    assert receiver_view["volunteer"]["full_name"] == "Ali"  # the receiver sees who is coming
