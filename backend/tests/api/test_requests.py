"""Stage 4b: food requests - create, read (role-scoped), edit, cancel, reject,
and what happens to requests when a listing ends."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import RequestStatus, UserRole, VerificationStatus
from app.domain.state_machines import REQUEST_TRANSITIONS, check_request_transition
from app.models.audit import AuditLog
from app.models.category import FoodCategory, ReceiverCategory
from app.models.listing import FoodListing
from app.models.profiles import ReceiverProfile
from app.models.request import FoodRequest
from app.models.user import User
from app.services import urgency_service
from tests.api.test_listings import _create, _make_provider, at, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
URL = "/api/v1/requests"


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser, db_session: Session) -> tuple[User, dict]:
    return _make_provider(client, make_user, db_session)


def _receiver(client: TestClient, make_user: MakeUser, db: Session, food: dict | None = None, *,
              verified: bool = True, profile: bool = True, prefs: list | None = None,
              name: str = "Hope Trust") -> tuple[User, dict]:
    user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED if verified else VerificationStatus.PENDING,
                     full_name=f"{name} contact")
    headers = login_headers(client, user.email)
    if profile:
        category = db.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == "ngo"))
        body = {"receiver_name": name, "receiver_category_id": str(category), "address": "Model Town, Lahore",
                "latitude": 31.48, "longitude": 74.32, "max_pickup_radius_km": 10}
        assert client.put("/api/v1/receivers/me", headers=headers, json=body).status_code == 200
    if prefs:
        response = client.put("/api/v1/receivers/me/food-preferences", headers=headers, json={"preferences": prefs})
        assert response.status_code == 200, response.text
    return user, headers


@pytest.fixture
def receiver(client: TestClient, make_user: MakeUser, db_session: Session) -> tuple[User, dict]:
    return _receiver(client, make_user, db_session)


@pytest.fixture
def listing(client: TestClient, provider: tuple, food: dict) -> dict:
    return _create(client, provider[1], bread(food))  # 30 packages, pickup window from +1 h to +4 h


def _request(client: TestClient, headers: dict, listing_id: str, quantity: float = 10, **extra) -> object:  # noqa: ANN003
    return client.post(URL, headers=headers, json={"listing_id": listing_id, "requested_quantity": quantity, **extra})


def _ok(client: TestClient, headers: dict, listing_id: str, quantity: float = 10, **extra) -> dict:  # noqa: ANN003
    response = _request(client, headers, listing_id, quantity, **extra)
    assert response.status_code == 201, response.text
    return response.json()


# ======================================================================= create


def test_create_request(client: TestClient, receiver: tuple, listing: dict, provider: tuple) -> None:
    data = _ok(client, receiver[1], listing["id"], 10, purpose="  Dinner   for 40 children ",
               collection_preference="needs_volunteer", preferred_pickup_at=at(2))
    assert data["status"] == "pending" and data["requested_quantity"] == 10 and data["version"] == 1
    assert data["purpose"] == "Dinner for 40 children"
    assert data["collection_preference"] == "needs_volunteer"
    assert data["listing"]["food_name"] == "Fresh bread" and data["listing"]["remaining_quantity"] == 30
    assert data["receiver"]["receiver_name"] == "Hope Trust" and data["receiver"]["category_name"] == "Welfare organization / NGO"
    assert data["receiver"]["contact_phone"] == receiver[0].phone
    assert 5 < data["distance_km"] < 7  # Gulberg pickup -> Model Town receiver
    history = client.get(f"{URL}/{data['id']}/history", headers=receiver[1]).json()
    assert [(h["from_status"], h["to_status"]) for h in history] == [(None, "pending")]


def test_who_may_request(client: TestClient, make_user: MakeUser, db_session: Session, listing: dict,
                         provider: tuple) -> None:
    assert _request(client, {}, listing["id"]).status_code == 401
    assert _request(client, provider[1], listing["id"]).status_code == 403

    _, unverified = _receiver(client, make_user, db_session, verified=False)
    response = _request(client, unverified, listing["id"])
    assert response.status_code == 403 and _code(response) == "VERIFICATION_REQUIRED"

    _, no_profile = _receiver(client, make_user, db_session, profile=False)
    response = _request(client, no_profile, listing["id"])
    assert response.status_code == 409 and _code(response) == "PROFILE_REQUIRED"

    ineligible_user, ineligible = _receiver(client, make_user, db_session)
    db_session.execute(update(ReceiverProfile).where(ReceiverProfile.user_id == ineligible_user.id)
                       .values(is_eligible=False))
    db_session.commit()
    response = _request(client, ineligible, listing["id"])
    assert response.status_code == 403 and _code(response) == "RECEIVER_NOT_ELIGIBLE"


def test_only_available_listings_can_be_requested(
    client: TestClient, receiver: tuple, provider: tuple, food: dict, db_session: Session
) -> None:
    paused = _create(client, provider[1], bread(food))
    client.post(f"/api/v1/listings/{paused['id']}/pause", headers=provider[1])
    gone = _create(client, provider[1], bread(food))
    db_session.execute(update(FoodListing).where(FoodListing.id == gone["id"]).values(allocated_quantity=30))
    late = _create(client, provider[1], bread(food))
    past = datetime.now(UTC) - timedelta(minutes=5)
    db_session.execute(update(FoodListing).where(FoodListing.id == late["id"]).values(
        pickup_window_start=past - timedelta(hours=2), pickup_window_end=past, collection_deadline=past))
    db_session.commit()
    for listing_id in (paused["id"], gone["id"], late["id"]):
        response = _request(client, receiver[1], listing_id)
        assert response.status_code == 409 and _code(response) == "LISTING_NOT_AVAILABLE"

    removed = _create(client, provider[1], bread(food))
    client.delete(f"/api/v1/listings/{removed['id']}", headers=provider[1])
    assert _request(client, receiver[1], removed["id"]).status_code == 404
    assert _request(client, receiver[1], "00000000-0000-0000-0000-000000000000").status_code == 404


@pytest.mark.parametrize(
    ("quantity", "status", "code"),
    [(31, 422, "QUANTITY_EXCEEDS_AVAILABLE"), (2.5, 422, "WHOLE_NUMBER_REQUIRED"), (0, 422, "VALIDATION_ERROR"),
     (-1, 422, "VALIDATION_ERROR")],
)
def test_quantity_rules(client: TestClient, receiver: tuple, listing: dict, quantity: float, status: int,
                        code: str) -> None:
    response = _request(client, receiver[1], listing["id"], quantity)
    assert response.status_code == status and _code(response) == code


def test_quantity_counts_what_is_left(client: TestClient, receiver: tuple, listing: dict, db_session: Session) -> None:
    db_session.execute(update(FoodListing).values(allocated_quantity=25))
    db_session.commit()
    response = _request(client, receiver[1], listing["id"], 6)
    assert response.status_code == 422 and response.json()["error"]["details"]["available"] == 5
    assert _request(client, receiver[1], listing["id"], 5).status_code == 201


def test_capacity_from_food_preferences(client: TestClient, make_user: MakeUser, db_session: Session,
                                        food: dict, listing: dict) -> None:
    prefs = [{"food_category_id": food["bakery_items"], "max_quantity_per_pickup": 20, "unit": "package"}]
    _, headers = _receiver(client, make_user, db_session, prefs=prefs)
    response = _request(client, headers, listing["id"], 25)
    assert response.status_code == 422 and _code(response) == "EXCEEDS_CAPACITY"
    assert response.json()["error"]["details"]["capacity"] == 20
    assert _request(client, headers, listing["id"], 20).status_code == 201

    # A capacity in a DIFFERENT unit can't be compared (plan H1), so it isn't applied.
    kg_prefs = [{"food_category_id": food["bakery_items"], "max_quantity_per_pickup": 2, "unit": "kg"}]
    _, kg_headers = _receiver(client, make_user, db_session, prefs=kg_prefs, name="Kg Trust")
    assert _request(client, kg_headers, listing["id"], 25).status_code == 201


def test_preferred_time_must_be_in_the_pickup_window(client: TestClient, receiver: tuple, listing: dict) -> None:
    response = _request(client, receiver[1], listing["id"], 5, preferred_pickup_at=at(6))
    assert response.status_code == 422 and _code(response) == "OUTSIDE_PICKUP_WINDOW"
    naive = _request(client, receiver[1], listing["id"], 5, preferred_pickup_at="2026-12-01T10:00:00")
    assert naive.status_code == 422


def test_duplicates_are_blocked_until_cancelled(client: TestClient, receiver: tuple, listing: dict) -> None:
    first = _ok(client, receiver[1], listing["id"])
    again = _request(client, receiver[1], listing["id"], 5)
    assert again.status_code == 409 and _code(again) == "DUPLICATE_REQUEST"
    assert again.json()["error"]["details"]["request_id"] == first["id"]
    client.post(f"{URL}/{first['id']}/cancel", headers=receiver[1])
    assert _request(client, receiver[1], listing["id"], 5).status_code == 201  # allowed after cancelling


def test_database_blocks_duplicate_active_requests(client: TestClient, receiver: tuple, listing: dict,
                                                   db_session: Session) -> None:
    made = _ok(client, receiver[1], listing["id"])
    original = db_session.get(FoodRequest, made["id"])
    db_session.add(FoodRequest(listing_id=original.listing_id, receiver_id=original.receiver_id, requested_quantity=1))
    with pytest.raises(IntegrityError, match="uq_food_requests_one_active_per_receiver"):
        db_session.commit()
    db_session.rollback()


# ====================================================================== reading


def test_each_role_sees_only_its_own_requests(
    client: TestClient, make_user: MakeUser, db_session: Session, provider: tuple, food: dict, listing: dict
) -> None:
    _, alice = _receiver(client, make_user, db_session, name="Alice Kitchen")
    _, bilal = _receiver(client, make_user, db_session, name="Bilal Trust")
    _, other_provider = _make_provider(client, make_user, db_session)
    other_listing = _create(client, other_provider, bread(food, food_name="Other bread"))
    mine = _ok(client, alice, listing["id"])
    _ok(client, bilal, listing["id"])
    _ok(client, alice, other_listing["id"])

    def ids(headers: dict, query: str = "") -> set[str]:
        response = client.get(f"{URL}{query}", headers=headers)
        assert response.status_code == 200, response.text
        return {(r["listing"]["food_name"], r["receiver"]["receiver_name"]) for r in response.json()["items"]}

    assert ids(alice) == {("Fresh bread", "Alice Kitchen"), ("Other bread", "Alice Kitchen")}
    assert ids(provider[1]) == {("Fresh bread", "Alice Kitchen"), ("Fresh bread", "Bilal Trust")}
    assert ids(other_provider) == {("Other bread", "Alice Kitchen")}
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    assert len(ids(admin)) == 3
    assert ids(alice, f"?listing_id={listing['id']}") == {("Fresh bread", "Alice Kitchen")}
    assert ids(alice, "?status=cancelled") == set()

    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)
    assert client.get(URL, headers=volunteer).status_code == 403
    assert client.get(f"{URL}/{mine['id']}", headers=bilal).status_code == 404  # not involved
    assert client.get(f"{URL}/{mine['id']}", headers=other_provider).status_code == 404
    assert client.get(f"{URL}/{mine['id']}", headers=provider[1]).status_code == 200


def test_incoming_requests_for_a_listing(client: TestClient, make_user: MakeUser, db_session: Session,
                                         provider: tuple, listing: dict) -> None:
    _, first = _receiver(client, make_user, db_session, name="First")
    _, second = _receiver(client, make_user, db_session, name="Second")
    _ok(client, first, listing["id"], 15)
    _ok(client, second, listing["id"], 10)
    url = f"/api/v1/listings/{listing['id']}/requests"

    rows = client.get(url, headers=provider[1]).json()
    assert [r["receiver"]["receiver_name"] for r in rows] == ["First", "Second"]  # first come first
    assert all(r["receiver"]["contact_phone"].startswith("+92") for r in rows)  # provider may contact them

    _, other_provider = _make_provider(client, make_user, db_session)
    assert client.get(url, headers=other_provider).status_code == 404
    assert client.get(url, headers=first).status_code == 403


# ============================================================ edit and cancel


def test_edit_pending_request(client: TestClient, receiver: tuple, listing: dict, make_user: MakeUser,
                              db_session: Session) -> None:
    made = _ok(client, receiver[1], listing["id"])
    body = {"requested_quantity": 12, "purpose": "More children today", "collection_preference": "self_pickup"}
    edited = client.patch(f"{URL}/{made['id']}", headers=receiver[1], json=body)
    assert edited.status_code == 200 and edited.json()["requested_quantity"] == 12 and edited.json()["version"] == 2

    too_much = client.patch(f"{URL}/{made['id']}", headers=receiver[1], json={**body, "requested_quantity": 40})
    assert too_much.status_code == 422 and _code(too_much) == "QUANTITY_EXCEEDS_AVAILABLE"

    _, stranger = _receiver(client, make_user, db_session, name="Stranger")
    assert client.patch(f"{URL}/{made['id']}", headers=stranger, json=body).status_code == 404


def test_cancel(client: TestClient, receiver: tuple, listing: dict) -> None:
    made = _ok(client, receiver[1], listing["id"])
    cancelled = client.post(f"{URL}/{made['id']}/cancel", headers=receiver[1], json={"reason": "Got food elsewhere"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    again = client.post(f"{URL}/{made['id']}/cancel", headers=receiver[1])
    assert again.status_code == 409 and _code(again) == "INVALID_STATUS_TRANSITION"
    edit = client.patch(f"{URL}/{made['id']}", headers=receiver[1], json={"requested_quantity": 1})
    assert edit.status_code == 409 and _code(edit) == "REQUEST_NOT_EDITABLE"
    history = client.get(f"{URL}/{made['id']}/history", headers=receiver[1]).json()
    assert history[-1]["to_status"] == "cancelled" and history[-1]["reason"] == "Got food elsewhere"


# ======================================================================= reject


def test_provider_rejects_with_reason(client: TestClient, receiver: tuple, provider: tuple, listing: dict,
                                      make_user: MakeUser, db_session: Session) -> None:
    made = _ok(client, receiver[1], listing["id"])
    url = f"{URL}/{made['id']}/reject"
    assert client.post(url, headers=provider[1], json={"reason": "no"}).status_code == 422
    assert client.post(url, headers=receiver[1], json={"reason": "I reject myself"}).status_code == 403
    _, other_provider = _make_provider(client, make_user, db_session)
    assert client.post(url, headers=other_provider, json={"reason": "Not my listing"}).status_code == 404

    rejected = client.post(url, headers=provider[1], json={"reason": "We can only serve registered NGOs nearby."})
    assert rejected.status_code == 200
    seen_by_receiver = client.get(f"{URL}/{made['id']}", headers=receiver[1]).json()
    assert seen_by_receiver["status"] == "rejected"
    assert seen_by_receiver["rejection_reason"] == "We can only serve registered NGOs nearby."
    assert seen_by_receiver["responded_at"] is not None

    twice = client.post(url, headers=provider[1], json={"reason": "Again please"})
    assert twice.status_code == 409 and _code(twice) == "INVALID_STATUS_TRANSITION"


def test_admin_rejection_is_audited(client: TestClient, receiver: tuple, listing: dict, make_user: MakeUser,
                                    db_session: Session) -> None:
    made = _ok(client, receiver[1], listing["id"])
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    response = client.post(f"{URL}/{made['id']}/reject", headers=admin, json={"reason": "Duplicate organization"})
    assert response.status_code == 200
    log = db_session.scalar(select(AuditLog).where(AuditLog.action == "request.reject"))
    assert log.reason == "Duplicate organization" and log.after == {"status": "rejected"}


# ====================================================== when the listing ends


def test_closing_a_listing_expires_its_pending_requests(
    client: TestClient, make_user: MakeUser, db_session: Session, provider: tuple, listing: dict
) -> None:
    _, waiting = _receiver(client, make_user, db_session, name="Waiting")
    _, withdrew = _receiver(client, make_user, db_session, name="Withdrew")
    pending = _ok(client, waiting, listing["id"])
    cancelled = _ok(client, withdrew, listing["id"])
    client.post(f"{URL}/{cancelled['id']}/cancel", headers=withdrew)

    client.post(f"/api/v1/listings/{listing['id']}/close", headers=provider[1])
    assert client.get(f"{URL}/{pending['id']}", headers=waiting).json()["status"] == "expired"
    assert client.get(f"{URL}/{cancelled['id']}", headers=withdrew).json()["status"] == "cancelled"  # untouched
    last = client.get(f"{URL}/{pending['id']}/history", headers=waiting).json()[-1]
    assert last["to_status"] == "expired" and last["reason"] == "listing closed by the provider"


def test_requested_listing_cannot_be_deleted(client: TestClient, receiver: tuple, provider: tuple,
                                             listing: dict) -> None:
    _ok(client, receiver[1], listing["id"])
    response = client.delete(f"/api/v1/listings/{listing['id']}", headers=provider[1])
    assert response.status_code == 409 and _code(response) == "LISTING_HAS_REQUESTS"


def test_expiry_scan_also_expires_requests(client: TestClient, receiver: tuple, listing: dict,
                                           db_session: Session) -> None:
    made = _ok(client, receiver[1], listing["id"])
    past = datetime.now(UTC) - timedelta(minutes=5)
    db_session.execute(update(FoodListing).values(pickup_window_start=past - timedelta(hours=2),
                                                  pickup_window_end=past, collection_deadline=past))
    db_session.commit()
    result = urgency_service.scan(db_session)
    assert result["expired"] == 1 and result["expired_requests"] == 1
    request = client.get(f"{URL}/{made['id']}", headers=receiver[1]).json()
    assert request["status"] == "expired"
    last = client.get(f"{URL}/{made['id']}/history", headers=receiver[1]).json()[-1]
    assert last["changed_by"] is None and last["reason"].startswith("the listing expired: Deadline passed")


@pytest.mark.parametrize("current", list(RequestStatus))
def test_request_state_machine_table(current: RequestStatus) -> None:
    for target in RequestStatus:
        if target in REQUEST_TRANSITIONS[current]:
            check_request_transition(current, target)
        else:
            with pytest.raises(AppError):
                check_request_transition(current, target)
