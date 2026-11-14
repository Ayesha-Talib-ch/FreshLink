"""Stage 6a: collections - created on allocation, handover, receipt, failure, scheduling."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ReceiverCategory
from app.models.matching import MatchRecommendation
from tests.api.test_listings import _create, _make_provider, at, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


def _receiver(client: TestClient, make_user: MakeUser, db: Session, food: dict, name: str) -> dict:
    user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED, full_name=f"{name} contact")
    headers = login_headers(client, user.email)
    category = db.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == "ngo"))
    client.put("/api/v1/receivers/me", headers=headers, json={
        "receiver_name": name, "receiver_category_id": str(category), "address": f"{name} street, Lahore",
        "latitude": 31.48, "longitude": 74.32})
    client.put("/api/v1/receivers/me/food-preferences", headers=headers, json={"preferences": [
        {"food_category_id": food["bakery_items"], "max_quantity_per_pickup": 30, "unit": "package"}]})
    return headers


@pytest.fixture
def setup(client: TestClient, make_user: MakeUser, db_session: Session, food: dict) -> dict:
    """NGO requests 15 of 30 packages (wants a volunteer); the kitchen requests 15 (self pickup).
    Both are approved, so two collections exist."""
    ngo = _receiver(client, make_user, db_session, food, "Hope NGO")
    kitchen = _receiver(client, make_user, db_session, food, "Kitchen")
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, bread(food, total_quantity=30))
    ids = {}
    for key, headers, preference in (("ngo", ngo, "needs_volunteer"), ("kitchen", kitchen, "self_pickup")):
        request = client.post("/api/v1/requests", headers=headers, json={
            "listing_id": listing["id"], "requested_quantity": 15, "collection_preference": preference,
            "preferred_pickup_at": at(2)}).json()
        approved = client.post(f"/api/v1/requests/{request['id']}/approve", headers=provider)
        assert approved.status_code == 200, approved.text
        ids[key] = request["id"]
    collections = client.get("/api/v1/collections", headers=provider).json()["items"]
    by_request = {c["request_id"]: c for c in collections}
    return {"provider": provider, "ngo": ngo, "kitchen": kitchen, "listing": listing, "requests": ids,
            "ngo_collection": by_request[ids["ngo"]], "kitchen_collection": by_request[ids["kitchen"]]}


def url(collection: dict, action: str = "") -> str:
    return f"/api/v1/collections/{collection['id']}" + (f"/{action}" if action else "")


# ======================================================================= created


def test_approval_creates_a_scheduled_collection_with_both_contacts(client: TestClient, setup: dict) -> None:
    c = setup["ngo_collection"]
    assert c["status"] == "scheduled" and c["approved_quantity"] == 15
    assert c["method"] == "volunteer"  # from the request's preference
    assert setup["kitchen_collection"]["method"] == "self_pickup"
    assert c["scheduled_pickup_at"] is not None
    # Inside this authorized transaction both sides see each other's contact details.
    seen_by_receiver = client.get(url(c), headers=setup["ngo"]).json()
    assert seen_by_receiver["provider"]["contact_phone"] == "+923001234567"
    assert seen_by_receiver["provider"]["address"] == "Shop 4, Gulberg III, Lahore"
    seen_by_provider = client.get(url(c), headers=setup["provider"]).json()
    assert seen_by_provider["receiver"]["receiver_name"] == "Hope NGO"
    assert seen_by_provider["receiver"]["contact_phone"].startswith("+92")


# ============================================================== the full journey


def test_handover_then_receipt_completes_the_redistribution(client: TestClient, setup: dict,
                                                            db_session: Session) -> None:
    c = setup["ngo_collection"]
    handed = client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 15, "notes": " in 3 crates "})
    assert handed.status_code == 200, handed.text
    assert handed.json()["status"] == "handed_over" and handed.json()["handed_over_quantity"] == 15
    assert handed.json()["notes"] == "in 3 crates"

    received = client.post(url(c, "receipt"), headers=setup["ngo"], json={"quantity": 14, "notes": "1 package damaged"})
    assert received.status_code == 200, received.text
    data = received.json()
    assert data["status"] == "received" and data["received_quantity"] == 14 and data["handed_over_quantity"] == 15

    request = client.get(f"/api/v1/requests/{setup['requests']['ngo']}", headers=setup["ngo"]).json()
    assert request["status"] == "completed"
    assert request["allocation"]["status"] == "completed"
    history = client.get(f"/api/v1/requests/{setup['requests']['ngo']}/history", headers=setup["ngo"]).json()
    assert history[-1]["to_status"] == "completed" and history[-1]["reason"] == "received 14 packages"
    # The matching snapshot learns the outcome (training data for the AI stage).
    assert "completed" in set(db_session.scalars(select(MatchRecommendation.outcome)))


# ================================================================ handover rules


def test_handover_rules(client: TestClient, setup: dict) -> None:
    c = setup["ngo_collection"]
    assert client.post(url(c, "handover"), headers=setup["ngo"], json={"quantity": 5}).status_code == 403
    too_much = client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 16})
    assert too_much.status_code == 422 and _code(too_much) == "QUANTITY_TOO_HIGH"
    half = client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 2.5})
    assert half.status_code == 422 and _code(half) == "WHOLE_NUMBER_REQUIRED"
    assert client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 0}).status_code == 422
    assert client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 12}).status_code == 200
    twice = client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 12})
    assert twice.status_code == 409 and _code(twice) == "INVALID_STATUS_TRANSITION"


# ================================================================= receipt rules


def test_receipt_rules(client: TestClient, setup: dict, make_user: MakeUser, db_session: Session) -> None:
    c = setup["ngo_collection"]
    early = client.post(url(c, "receipt"), headers=setup["ngo"], json={"quantity": 5})
    assert early.status_code == 409  # nothing handed over yet
    client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 10})

    assert client.post(url(c, "receipt"), headers=setup["provider"], json={"quantity": 10}).status_code == 403
    more = client.post(url(c, "receipt"), headers=setup["ngo"], json={"quantity": 11})
    assert more.status_code == 422 and _code(more) == "QUANTITY_TOO_HIGH"  # can't receive more than handed over

    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    no_reason = client.post(url(c, "receipt"), headers=admin, json={"quantity": 10})
    assert no_reason.status_code == 422 and _code(no_reason) == "REASON_REQUIRED"
    ok = client.post(url(c, "receipt"), headers=admin, json={"quantity": 10, "reason": "Receiver confirmed by phone"})
    assert ok.status_code == 200
    assert db_session.scalar(select(AuditLog.reason).where(AuditLog.action == "collection.receipt")) == \
        "Receiver confirmed by phone"


# ======================================================================= failure


def test_failure_before_handover_returns_the_food(client: TestClient, setup: dict, db_session: Session) -> None:
    listing_url = f"/api/v1/listings/{setup['listing']['id']}"
    assert client.get(listing_url, headers=setup["provider"]).json()["status"] == "fully_allocated"

    failed = client.post(url(setup["kitchen_collection"], "fail"), headers=setup["kitchen"],
                         json={"reason": "Our van broke down"})
    assert failed.status_code == 200 and failed.json()["status"] == "failed"
    assert failed.json()["failure_reason"] == "Our van broke down"

    listing = client.get(listing_url, headers=setup["provider"]).json()
    assert listing["allocated_quantity"] == 15 and listing["status"] == "active"  # back on offer
    request = client.get(f"/api/v1/requests/{setup['requests']['kitchen']}", headers=setup["kitchen"]).json()
    assert request["status"] == "cancelled" and request["allocation"]["status"] == "failed"
    assert db_session.scalar(select(AuditLog.action).where(AuditLog.action == "collection.fail")) is not None


def test_failure_after_handover_does_not_return_the_food(client: TestClient, setup: dict) -> None:
    c = setup["ngo_collection"]
    client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 15})
    client.post(url(c, "fail"), headers=setup["provider"], json={"reason": "Lost in transit"})
    listing = client.get(f"/api/v1/listings/{setup['listing']['id']}", headers=setup["provider"]).json()
    assert listing["allocated_quantity"] == 30  # it left the provider; it can't be offered again
    assert client.post(url(c, "fail"), headers=setup["provider"], json={"reason": "Again please"}).status_code == 409


# ==================================================================== scheduling


def test_scheduling(client: TestClient, setup: dict, make_user: MakeUser, db_session: Session) -> None:
    c = setup["kitchen_collection"]
    changed = client.patch(url(c), headers=setup["kitchen"], json={"method": "volunteer", "scheduled_pickup_at": at(3)})
    assert changed.status_code == 200 and changed.json()["method"] == "volunteer" and changed.json()["version"] == 2
    outside = client.patch(url(c), headers=setup["provider"], json={"scheduled_pickup_at": at(30)})
    assert outside.status_code == 422 and _code(outside) == "OUTSIDE_PICKUP_WINDOW"

    stranger = login_headers(client, make_user(role=UserRole.RECEIVER, verification_status=APPROVED).email)
    assert client.patch(url(c), headers=stranger, json={"method": "self_pickup"}).status_code == 404

    client.post(url(c, "handover"), headers=setup["provider"], json={"quantity": 15})
    late = client.patch(url(c), headers=setup["kitchen"], json={"method": "self_pickup"})
    assert late.status_code == 409 and _code(late) == "COLLECTION_NOT_EDITABLE"


# ========================================================= allocation cancelling


def test_cancelling_an_allocation_cancels_its_collection(client: TestClient, setup: dict) -> None:
    ngo_allocation = client.get(f"/api/v1/requests/{setup['requests']['ngo']}",
                                headers=setup["provider"]).json()["allocation"]["id"]
    kitchen_allocation = client.get(f"/api/v1/requests/{setup['requests']['kitchen']}",
                                    headers=setup["provider"]).json()["allocation"]["id"]
    ok = client.post(f"/api/v1/allocations/{ngo_allocation}/cancel", headers=setup["provider"],
                     json={"reason": "NGO closed today"})
    assert ok.status_code == 200
    assert client.get(url(setup["ngo_collection"]), headers=setup["provider"]).json()["status"] == "cancelled"

    client.post(url(setup["kitchen_collection"], "handover"), headers=setup["provider"], json={"quantity": 15})
    too_late = client.post(f"/api/v1/allocations/{kitchen_allocation}/cancel", headers=setup["provider"],
                           json={"reason": "Changed my mind"})
    assert too_late.status_code == 409 and _code(too_late) == "ALLOCATION_NOT_CANCELLABLE"


# ======================================================================= reading


def test_each_role_sees_only_its_own_collections(client: TestClient, setup: dict, make_user: MakeUser,
                                                 db_session: Session) -> None:
    assert client.get("/api/v1/collections", headers=setup["provider"]).json()["total"] == 2
    assert client.get("/api/v1/collections", headers=setup["ngo"]).json()["total"] == 1
    _, other_provider = _make_provider(client, make_user, db_session)
    assert client.get("/api/v1/collections", headers=other_provider).json()["total"] == 0
    assert client.get(url(setup["ngo_collection"]), headers=other_provider).status_code == 404
    assert client.get(url(setup["ngo_collection"]), headers=setup["kitchen"]).status_code == 404
    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)
    assert client.get("/api/v1/collections", headers=volunteer).status_code == 403
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    assert client.get("/api/v1/collections?status=scheduled", headers=admin).json()["total"] == 2


def test_database_refuses_receiving_more_than_handed_over(client: TestClient, setup: dict,
                                                          db_session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_collections_received_within_handed_over"):
        db_session.execute(text("UPDATE collections SET handed_over_quantity = 5, received_quantity = 6"))
        db_session.commit()
    db_session.rollback()
