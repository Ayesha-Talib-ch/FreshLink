"""Stage 8a: role dashboards (/me/dashboard), the admin dashboard and the activity feed.
Every counter is checked against what the test itself created."""

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.listing import FoodListing
from app.models.verification import VerificationRequest
from tests.api.test_assignments import _volunteer, world  # noqa: F401  (fixture)
from tests.api.test_listings import _create, biryani, bread
from tests.api.test_notifications import _request, food, scene  # noqa: F401  (fixtures)
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
ME = "/api/v1/me/dashboard"


def _dash(client: TestClient, headers: dict) -> dict:
    response = client.get(ME, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_provider_dashboard_follows_the_flow(client: TestClient, scene: dict, food: dict) -> None:
    provider = scene["provider"]
    other = _create(client, provider, biryani(food))
    first = _dash(client, provider)
    assert first["role"] == "provider" and first["profile_complete"] is True
    assert (first["total_listings"], first["active_listings"], first["pending_requests"]) == (2, 2, 0)

    request = _request(client, scene, 10)
    assert _dash(client, provider)["pending_requests"] == 1
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=provider)
    dash = _dash(client, provider)
    assert (dash["pending_requests"], dash["approved_requests"]) == (0, 1)
    assert dash["recent_notifications"][0]["title"] == "New food request" and dash["unread_notifications"] == 1

    collection = client.get("/api/v1/collections", headers=provider).json()["items"][0]
    client.post(f"/api/v1/collections/{collection['id']}/handover", headers=provider, json={"quantity": 10})
    client.post(f"/api/v1/collections/{collection['id']}/receipt", headers=scene["receiver"], json={"quantity": 10})
    client.post(f"/api/v1/listings/{other['id']}/close", headers=provider)
    dash = _dash(client, provider)
    assert dash["completed_distributions"] == 1 and dash["approved_requests"] == 0  # the request is completed
    assert (dash["active_listings"], dash["expired_or_cancelled_listings"]) == (1, 1)


def test_urgent_listings_counter(client: TestClient, scene: dict, db_session: Session) -> None:
    db_session.execute(update(FoodListing).values(urgency_level="medium"))
    db_session.commit()
    assert _dash(client, scene["provider"])["urgent_listings"] == 0
    db_session.execute(update(FoodListing).values(urgency_level="high"))
    db_session.commit()
    assert _dash(client, scene["provider"])["urgent_listings"] == 1


def test_receiver_dashboard(client: TestClient, scene: dict, food: dict) -> None:
    receiver, provider = scene["receiver"], scene["provider"]
    far = _create(client, provider, bread(food, food_name="Far bread"))
    dash = _dash(client, receiver)
    assert dash["role"] == "receiver" and dash["available_food"] == 2  # both inside the 10 km radius
    assert dash["recent_notifications"][0]["title"] == "Food you may need"

    first = _request(client, scene, 5)
    second = client.post("/api/v1/requests", headers=receiver,
                         json={"listing_id": far["id"], "requested_quantity": 5}).json()
    dash = _dash(client, receiver)
    assert (dash["active_requests"], dash["pending_requests"]) == (2, 2)
    client.post(f"/api/v1/requests/{second['id']}/reject", headers=provider, json={"reason": "Already promised"})
    client.post(f"/api/v1/requests/{first['id']}/approve", headers=provider)
    dash = _dash(client, receiver)
    assert (dash["pending_requests"], dash["approved_requests"], dash["rejected_requests"]) == (0, 1, 1)
    assert dash["active_requests"] == 1 and dash["completed_collections"] == 0


def test_volunteer_dashboard(client: TestClient, make_user: MakeUser, world: dict) -> None:  # noqa: F811
    _, volunteer = _volunteer(client, make_user, "Bilal")
    dash = _dash(client, volunteer)
    assert dash["role"] == "volunteer" and dash["available_assignments"] == 1
    task = world["task"]["id"]
    client.post(f"/api/v1/assignments/{task}/claim", headers=volunteer)
    dash = _dash(client, volunteer)
    assert (dash["available_assignments"], dash["accepted_tasks"], dash["active_collections"]) == (0, 1, 0)
    client.post(f"/api/v1/assignments/{task}/status", headers=volunteer, json={"status": "en_route_pickup"})
    dash = _dash(client, volunteer)
    assert (dash["accepted_tasks"], dash["active_collections"], dash["completed_deliveries"]) == (0, 1, 0)

    _, far_away = _volunteer(client, make_user, "Far", lat=31.2, lng=74.0, radius=5)
    assert _dash(client, far_away)["available_assignments"] == 0  # outside their service area


def test_no_profile_yet_gives_zeros(client: TestClient, make_user: MakeUser) -> None:
    for role in (UserRole.PROVIDER, UserRole.RECEIVER, UserRole.VOLUNTEER):
        dash = _dash(client, login_headers(client, make_user(role=role).email))  # not approved, no profile
        assert dash["role"] == role.value and dash["profile_complete"] is False
        assert all(v == 0 for k, v in dash.items() if isinstance(v, int) and not isinstance(v, bool))
    assert client.get(ME).status_code == 401


def test_admin_dashboard_metrics(client: TestClient, scene: dict, make_user: MakeUser, food: dict,
                                 db_session: Session) -> None:
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    volunteer = make_user(role=UserRole.VOLUNTEER, verification_status=VerificationStatus.PENDING)
    db_session.add(VerificationRequest(user_id=volunteer.id))
    db_session.commit()
    _create(client, scene["provider"], biryani(food))  # 12.5 kg
    mistake = _create(client, scene["provider"], biryani(food, food_name="Typo listing"))
    assert client.delete(f"/api/v1/listings/{mistake['id']}", headers=scene["provider"]).status_code == 204
    request = _request(client, scene, 10)
    client.post(f"/api/v1/requests/{request['id']}/approve", headers=scene["provider"])
    collection = client.get("/api/v1/collections", headers=scene["provider"]).json()["items"][0]
    client.post(f"/api/v1/collections/{collection['id']}/handover", headers=scene["provider"], json={"quantity": 10})
    client.post(f"/api/v1/collections/{collection['id']}/receipt", headers=scene["receiver"], json={"quantity": 9})
    _request(client, scene, 3)  # a second request, still pending

    d = client.get("/api/v1/admin/dashboard", headers=admin).json()
    assert (d["total_users"], d["total_providers"], d["total_receivers"], d["total_volunteers"]) == (4, 1, 1, 1)
    assert d["pending_verifications"] == 1
    assert (d["total_listings"], d["active_listings"], d["expired_and_cancelled_listings"]) == (2, 2, 0)
    assert (d["total_requests"], d["pending_requests"], d["approved_requests"]) == (2, 1, 0)
    assert d["completed_distributions"] == 1
    assert d["quantity_listed"] == [{"unit": "kg", "quantity": "12.50"}, {"unit": "package", "quantity": "30.00"}]
    assert d["quantity_allocated"] == [{"unit": "package", "quantity": "10.00"}]
    assert d["quantity_collected"] == [{"unit": "package", "quantity": "10.00"}]
    assert d["quantity_distributed"] == [{"unit": "package", "quantity": "9.00"}]  # what the receiver confirmed
    summaries = [a["summary"] for a in d["recent_activity"]]
    assert any("received 9 packages of Fresh bread" in s for s in summaries)
    assert client.get(ME, headers=admin).json()["total_users"] == 4  # /me/dashboard gives the same shape

    assert client.get("/api/v1/admin/dashboard", headers=scene["provider"]).status_code == 403


def test_activity_feed(client: TestClient, scene: dict, make_user: MakeUser) -> None:
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    request = _request(client, scene, 4)
    client.post(f"/api/v1/requests/{request['id']}/reject", headers=scene["provider"], json={"reason": "Too far"})
    feed = client.get("/api/v1/admin/activity?limit=50", headers=admin).json()
    kinds = {item["kind"] for item in feed}
    assert {"user.registered", "listing.created", "request.created", "request.status"} <= kinds
    assert any(i["summary"] == "Hope NGO requested 4 packages of Fresh bread" for i in feed)
    assert any(i["summary"].endswith("pending → rejected") for i in feed)
    assert [i["at"] for i in feed] == sorted((i["at"] for i in feed), reverse=True)  # newest first
    assert len(client.get("/api/v1/admin/activity?limit=2", headers=admin).json()) == 2
    assert client.get("/api/v1/admin/activity", headers=scene["receiver"]).status_code == 403
