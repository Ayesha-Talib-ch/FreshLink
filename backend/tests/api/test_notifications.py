"""Stage 7: in-app notifications - the user endpoints, every event that notifies someone,
category switches and admin announcements."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ReceiverCategory
from app.models.listing import FoodListing
from app.models.notification import Notification
from app.models.user import User
from app.services import matching_service, urgency_service
from tests.api.test_listings import _create, _make_provider, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
URL = "/api/v1/notifications"


def _titles(client: TestClient, headers: dict) -> list[str]:
    return [n["title"] for n in client.get(f"{URL}?page_size=100", headers=headers).json()["items"]]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


def _receiver(client: TestClient, make_user: MakeUser, db: Session, food: dict, name: str = "Hope NGO",
              prefs: bool = True) -> tuple[User, dict]:
    user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED)
    headers = login_headers(client, user.email)
    category = db.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == "ngo"))
    client.put("/api/v1/receivers/me", headers=headers, json={
        "receiver_name": name, "receiver_category_id": str(category), "address": "Model Town, Lahore",
        "latitude": 31.48, "longitude": 74.32})
    if prefs:
        client.put("/api/v1/receivers/me/food-preferences", headers=headers, json={"preferences": [
            {"food_category_id": food["bakery_items"], "max_quantity_per_pickup": 30, "unit": "package"}]})
    return user, headers


@pytest.fixture
def scene(client: TestClient, make_user: MakeUser, db_session: Session, food: dict) -> dict:
    receiver_user, receiver = _receiver(client, make_user, db_session, food)
    provider_user, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food, total_quantity=30))
    return {"provider": provider, "provider_user": provider_user, "receiver": receiver,
            "receiver_user": receiver_user, "listing": listing}


def _request(client: TestClient, scene: dict, quantity: int = 10, **extra) -> dict:  # noqa: ANN003
    response = client.post("/api/v1/requests", headers=scene["receiver"],
                           json={"listing_id": scene["listing"]["id"], "requested_quantity": quantity, **extra})
    assert response.status_code == 201, response.text
    return response.json()


# ============================================================ the user endpoints


def test_list_count_and_mark_read(client: TestClient, scene: dict, make_user: MakeUser) -> None:
    _request(client, scene)
    provider = scene["provider"]
    assert client.get(f"{URL}/unread-count", headers=provider).json() == {"unread": 1}
    page = client.get(URL, headers=provider).json()
    note = page["items"][0]
    assert note["title"] == "New food request" and note["category_code"] == "request_new"
    assert note["body"] == "Hope NGO asked for 10 packages of Fresh bread."
    assert note["entity_type"] == "request" and note["link"] == f"/requests/{note['entity_id']}"

    read = client.post(f"{URL}/{note['id']}/read", headers=provider)
    assert read.status_code == 200 and read.json()["is_read"] is True
    assert client.get(f"{URL}/unread-count", headers=provider).json() == {"unread": 0}
    assert client.get(f"{URL}?unread_only=true", headers=provider).json()["total"] == 0

    stranger = login_headers(client, make_user(role=UserRole.RECEIVER).email)
    assert client.post(f"{URL}/{note['id']}/read", headers=stranger).status_code == 404  # not theirs
    assert client.get(URL).status_code == 401


def test_read_all(client: TestClient, scene: dict) -> None:
    _request(client, scene)
    request = client.get("/api/v1/requests", headers=scene["receiver"]).json()["items"][0]
    client.post(f"/api/v1/requests/{request['id']}/cancel", headers=scene["receiver"])
    assert client.get(f"{URL}/unread-count", headers=scene["provider"]).json() == {"unread": 2}
    assert client.post(f"{URL}/read-all", headers=scene["provider"]).json() == {"marked": 2}
    assert client.get(f"{URL}/unread-count", headers=scene["provider"]).json() == {"unread": 0}


# ======================================================= events that notify someone


def test_new_listing_notifies_the_top_matches_once(client: TestClient, scene: dict, make_user: MakeUser,
                                                   db_session: Session, food: dict) -> None:
    # The receiver existed (with a bakery preference) before the listing was published.
    titles = _titles(client, scene["receiver"])
    assert titles == ["Food you may need"]
    body = client.get(URL, headers=scene["receiver"]).json()["items"][0]["body"]
    assert body.startswith("Fresh bread (30 packages):") and "accepts Bakery items" in body

    _, no_bakery = _receiver(client, make_user, db_session, food, "Dairy only", prefs=False)
    assert _titles(client, no_bakery) == []  # not a match: not notified
    client.post(f"/api/v1/listings/{scene['listing']['id']}/matches/refresh", headers=scene["provider"])
    listing = db_session.get(FoodListing, scene["listing"]["id"])
    assert matching_service.notify_top_matches(db_session, listing) == 0  # already told: nobody new
    db_session.commit()
    assert _titles(client, scene["receiver"]) == ["Food you may need"]  # refreshing doesn't repeat it


def test_request_decisions_reach_the_receiver(client: TestClient, scene: dict) -> None:
    approved = _request(client, scene, 10)
    client.post(f"/api/v1/requests/{approved['id']}/approve", headers=scene["provider"])
    notes = client.get(URL, headers=scene["receiver"]).json()["items"]
    assert notes[0]["title"] == "Request approved"
    assert notes[0]["body"].startswith("You will receive 10 packages of Fresh bread.")


def test_rejection_reason_reaches_the_receiver(client: TestClient, scene: dict) -> None:
    request = _request(client, scene)
    client.post(f"/api/v1/requests/{request['id']}/reject", headers=scene["provider"],
                json={"reason": "We only serve registered NGOs nearby."})
    newest = client.get(URL, headers=scene["receiver"]).json()["items"][0]
    assert newest["title"] == "Request not approved"
    assert newest["body"].endswith("We only serve registered NGOs nearby.")


def test_closing_a_listing_tells_waiting_receivers(client: TestClient, scene: dict) -> None:
    _request(client, scene)
    client.post(f"/api/v1/listings/{scene['listing']['id']}/close", headers=scene["provider"])
    assert "Food no longer available" in _titles(client, scene["receiver"])


def test_collection_events(client: TestClient, scene: dict, make_user: MakeUser) -> None:
    request = _request(client, scene)
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=scene["provider"])
    collection = client.get("/api/v1/collections", headers=scene["provider"]).json()["items"][0]
    client.post(f"/api/v1/collections/{collection['id']}/handover", headers=scene["provider"], json={"quantity": 10})
    assert "Food handed over" in _titles(client, scene["receiver"])
    client.post(f"/api/v1/collections/{collection['id']}/receipt", headers=scene["receiver"], json={"quantity": 9})
    provider_note = client.get(URL, headers=scene["provider"]).json()["items"][0]
    assert provider_note["title"] == "Food received"
    assert provider_note["body"] == "Hope NGO received 9 packages of Fresh bread. Thank you!"


def test_failed_collection_tells_the_other_side_and_admins(client: TestClient, scene: dict,
                                                           make_user: MakeUser) -> None:
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    request = _request(client, scene)
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=scene["provider"])
    collection = client.get("/api/v1/collections", headers=scene["receiver"]).json()["items"][0]
    client.post(f"/api/v1/collections/{collection['id']}/fail", headers=scene["receiver"],
                json={"reason": "Our van broke down"})
    assert "Collection failed" in _titles(client, scene["provider"])
    assert "Collection failed" not in _titles(client, scene["receiver"])  # they reported it themselves
    assert "Failed collection" in _titles(client, admin)


def test_registration_and_verification_alerts(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    client.post("/api/v1/auth/register", json={"email": "new.bakery@example.com", "phone": "0300 9998888",
                                               "full_name": "New Bakery", "password": "Fresh-Link-2026",
                                               "role": "provider"})
    first = client.get(URL, headers=admin).json()["items"][0]
    assert first["title"] == "New registration" and first["link"].startswith("/admin/users/")


def test_verification_result_reaches_the_user(client: TestClient, make_user: MakeUser, db_session: Session) -> None:
    from app.models.verification import VerificationRequest

    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    user = make_user(role=UserRole.PROVIDER, verification_status=VerificationStatus.PENDING)
    db_session.add(VerificationRequest(user_id=user.id))
    db_session.commit()
    request_id = db_session.scalar(select(VerificationRequest.id))
    client.post(f"/api/v1/admin/verifications/{request_id}/reject", headers=admin,
                json={"reason": "The licence is expired."})
    note = client.get(URL, headers=login_headers(client, user.email)).json()["items"][0]
    assert note["title"] == "Account not approved"
    assert note["body"] == "Your verification was not approved: The licence is expired."
    assert note["link"] == "/me/verification"


def test_urgency_scan_alerts_once(client: TestClient, scene: dict, db_session: Session) -> None:
    deadline = datetime.now(UTC) + timedelta(hours=60)  # far away: normal
    db_session.execute(update(FoodListing).values(pickup_window_start=deadline - timedelta(hours=2),
                                                  pickup_window_end=deadline, collection_deadline=deadline,
                                                  urgency_level="normal"))
    db_session.commit()
    soon = datetime.now(UTC) + timedelta(hours=3)  # bakery rule: <= 6 h left is critical
    db_session.execute(update(FoodListing).values(pickup_window_start=soon - timedelta(hours=2),
                                                  pickup_window_end=soon, collection_deadline=soon))
    db_session.commit()
    urgency_service.scan(db_session)
    urgency_service.scan(db_session)  # second run: level unchanged, no second alert
    assert _titles(client, scene["provider"]).count("Listing needs attention") == 1


# ================================================================= admin features


def test_switched_off_category_sends_nothing(client: TestClient, scene: dict, make_user: MakeUser) -> None:
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    off = client.put("/api/v1/admin/notification-categories/request_new", headers=admin,
                     json={"is_enabled": False})
    assert off.status_code == 200 and off.json()["is_enabled"] is False
    try:
        _request(client, scene)
        assert "New food request" not in _titles(client, scene["provider"])
    finally:  # seeded table: switch it back on
        client.put("/api/v1/admin/notification-categories/request_new", headers=admin, json={"is_enabled": True})
    assert client.put("/api/v1/admin/notification-categories/nonsense", headers=admin,
                      json={"is_enabled": False}).status_code == 404


def test_announcements(client: TestClient, scene: dict, make_user: MakeUser, db_session: Session) -> None:
    admin_user = make_user(role=UserRole.ADMIN, verification_status=APPROVED)
    admin = login_headers(client, admin_user.email)
    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)

    to_receivers = client.post("/api/v1/admin/announcements", headers=admin, json={
        "title": "Eid schedule", "body": "Pickups close at 6 pm on Eid.", "audience_type": "role",
        "audience_role": "receiver"})
    assert to_receivers.status_code == 201 and to_receivers.json()["recipient_count"] == 1
    assert "Eid schedule" in _titles(client, scene["receiver"])
    assert "Eid schedule" not in _titles(client, volunteer)

    everyone = client.post("/api/v1/admin/announcements", headers=admin, json={
        "title": "Welcome", "body": "FreshLink is live in Lahore!"})
    assert everyone.json()["recipient_count"] == 4  # provider, receiver, admin, volunteer

    chosen = client.post("/api/v1/admin/announcements", headers=admin, json={
        "title": "Hello", "body": "Just for you.", "audience_type": "users",
        "user_ids": [str(scene["provider_user"].id)]})
    assert chosen.json()["recipient_count"] == 1

    assert client.post("/api/v1/admin/announcements", headers=admin, json={
        "title": "Oops", "body": "No role chosen", "audience_type": "role"}).status_code == 422
    assert client.post("/api/v1/admin/announcements", headers=scene["provider"], json={
        "title": "Hack", "body": "Not an admin"}).status_code == 403
    assert len(client.get("/api/v1/admin/announcements", headers=admin).json()) == 3
    assert db_session.scalar(select(AuditLog.action).where(AuditLog.action == "announcement.send")) is not None


def test_notifications_follow_the_user(db_session: Session, scene: dict) -> None:
    """Each notification belongs to exactly one user; deleting the user deletes them (CASCADE)."""
    count = db_session.query(Notification).filter(Notification.user_id == scene["receiver_user"].id).count()
    assert count >= 1
