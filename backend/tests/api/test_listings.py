"""Stage 3a: food listings - create, read, edit (optimistic locking), status changes, photos."""

import threading
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import ListingStatus, UserRole, VerificationStatus
from app.domain.state_machines import LISTING_PROVIDER_TRANSITIONS, check_listing_transition
from app.models.category import FoodCategory, ProviderCategory
from app.models.listing import FoodListing
from app.models.user import User
from app.schemas.listings import ListingIn
from app.services import listing_service
from tests.conftest import MakeUser, login_headers

PKT = timezone(timedelta(hours=5), "PKT")  # Pakistan time
APPROVED = VerificationStatus.APPROVED
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%%EOF\n"


def at(hours: float) -> str:
    """A Pakistan-time timestamp `hours` from now, as the apps will send it."""
    return (datetime.now(PKT) + timedelta(hours=hours)).isoformat()


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def _field(response) -> str:  # noqa: ANN001
    details = response.json()["error"]["details"]
    return details.get("field") or details["fields"][0]["field"]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


def _make_provider(client: TestClient, make_user: MakeUser, db: Session, verified: bool = True,
                   profile: bool = True) -> tuple[User, dict]:
    user = make_user(role=UserRole.PROVIDER,
                     verification_status=APPROVED if verified else VerificationStatus.NOT_SUBMITTED)
    headers = login_headers(client, user.email)
    if profile:
        category = db.scalar(select(ProviderCategory.id).where(ProviderCategory.code == "bakery"))
        body = {"address": "Shop 4, Gulberg III, Lahore", "latitude": 31.52, "longitude": 74.35,
                "business_name": f"Bakers {user.phone[-4:]}", "provider_category_id": str(category),
                "contact_person": "Ali Raza", "contact_phone": "0300 1234567"}
        assert client.put("/api/v1/providers/me", headers=headers, json=body).status_code == 200
    return user, headers


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser, db_session: Session) -> tuple[User, dict]:
    return _make_provider(client, make_user, db_session)


def bread(food: dict, **overrides) -> dict:  # noqa: ANN003
    return {"food_category_id": food["bakery_items"], "food_name": "Fresh bread", "unit": "package",
            "total_quantity": 30, "pickup_window_start": at(1), "pickup_window_end": at(4),
            "declaration_accepted": True, **overrides}


def biryani(food: dict, **overrides) -> dict:  # noqa: ANN003
    return {"food_category_id": food["cooked_meals"], "food_name": "Chicken biryani", "unit": "kg",
            "total_quantity": 12.5, "preparation_at": at(-1), "pickup_window_start": at(0.5),
            "pickup_window_end": at(3), "declaration_accepted": True, **overrides}


def _create(client: TestClient, headers: dict, body: dict) -> dict:
    response = client.post("/api/v1/listings", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ======================================================================= create


def test_create_listing_uses_business_address(client: TestClient, provider: tuple, food: dict) -> None:
    response = client.post("/api/v1/listings", headers=provider[1], json=bread(food))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "active" and data["version"] == 1
    assert response.headers["etag"] == '"1"'
    assert data["total_quantity"] == 30 and data["allocated_quantity"] == 0 and data["remaining_quantity"] == 30
    assert data["pickup_address"] == "Shop 4, Gulberg III, Lahore" and data["pickup_latitude"] == 31.52
    assert data["collection_deadline"] == data["pickup_window_end"]  # default
    assert data["food_category"]["code"] == "bakery_items" and data["distribution_type"] == "free_donation"
    assert set(data["provider"]) == {"id", "business_name"}  # no phone / contact person exposed


def test_create_with_own_pickup_location(client: TestClient, provider: tuple, food: dict) -> None:
    body = bread(food, pickup_address="Back gate, Liberty Market", pickup_latitude=31.51, pickup_longitude=74.34)
    assert _create(client, provider[1], body)["pickup_address"] == "Back gate, Liberty Market"
    partial = client.post("/api/v1/listings", headers=provider[1], json=bread(food, pickup_address="Somewhere"))
    assert partial.status_code == 422


def test_create_cooked_meal(client: TestClient, provider: tuple, food: dict) -> None:
    data = _create(client, provider[1], biryani(food))
    assert data["unit"] == "kg" and data["total_quantity"] == 12.5 and data["preparation_at"] is not None


def test_only_verified_providers_with_profile_can_create(
    client: TestClient, make_user: MakeUser, db_session: Session, food: dict
) -> None:
    _, unverified = _make_provider(client, make_user, db_session, verified=False)
    response = client.post("/api/v1/listings", headers=unverified, json=bread(food))
    assert response.status_code == 403 and _code(response) == "VERIFICATION_REQUIRED"

    _, no_profile = _make_provider(client, make_user, db_session, profile=False)
    response = client.post("/api/v1/listings", headers=no_profile, json=bread(food))
    assert response.status_code == 409 and _code(response) == "PROFILE_REQUIRED"

    receiver = login_headers(client, make_user(role=UserRole.RECEIVER, verification_status=APPROVED).email)
    assert client.post("/api/v1/listings", headers=receiver, json=bread(food)).status_code == 403
    assert client.post("/api/v1/listings", json=bread(food)).status_code == 401


@pytest.mark.parametrize(
    ("make_body", "code", "field"),
    [
        (lambda f: biryani(f, preparation_at=None), "PREPARATION_TIME_REQUIRED", "preparation_at"),
        (lambda f: bread(f, food_category_id=f["dairy"], food_name="Milk", unit="litre"),
         "EXPIRY_DATE_REQUIRED", "expiry_at"),
        (lambda f: biryani(f, preparation_at=at(2)), "DATE_IN_FUTURE", "preparation_at"),
        (lambda f: bread(f, expiry_at=at(-1)), "DATE_ALREADY_PASSED", "expiry_at"),
        (lambda f: bread(f, pickup_window_start=at(-5), pickup_window_end=at(-2)), "DATE_ALREADY_PASSED",
         "collection_deadline"),
        (lambda f: bread(f, pickup_window_start=at(24 * 40), pickup_window_end=at(24 * 40 + 2)), "DATE_TOO_FAR",
         "collection_deadline"),
        (lambda f: bread(f, expiry_at=at(2)), "DEADLINE_AFTER_EXPIRY", "collection_deadline"),
        (lambda f: bread(f, food_category_id="00000000-0000-0000-0000-000000000000"), "CATEGORY_NOT_AVAILABLE",
         "food_category_id"),
    ],
)
def test_date_and_category_rules(client: TestClient, provider: tuple, food: dict, make_body, code: str, field: str) -> None:  # noqa: ANN001
    response = client.post("/api/v1/listings", headers=provider[1], json=make_body(food))
    assert response.status_code == 422, response.text
    assert _code(response) == code and _field(response) == field


@pytest.mark.parametrize(
    ("overrides", "message_part"),
    [
        ({"declaration_accepted": False}, "confirm the declaration"),
        ({"food_name": "  x "}, "Please enter the food name."),
        ({"total_quantity": 2.5}, "whole number"),  # packages can't be split
        ({"total_quantity": 0}, "greater than 0"),
        ({"pickup_window_end": "PLACEHOLDER_BEFORE_START"}, "must end after it starts"),
        ({"collection_deadline": "PLACEHOLDER_BEFORE_START"}, "can't be before the pickup window starts"),
        ({"pickup_window_start": "2026-12-01T10:00:00"}, "timezone"),  # no time zone
        ({"distribution_type": "sale"}, "free_donation"),
    ],
)
def test_input_validation(client: TestClient, provider: tuple, food: dict, overrides: dict, message_part: str) -> None:
    body = bread(food)
    for key, value in overrides.items():
        body[key] = at(0.5) if value == "PLACEHOLDER_BEFORE_START" else value
    response = client.post("/api/v1/listings", headers=provider[1], json=body)
    assert response.status_code == 422, response.text
    assert message_part in response.json()["error"]["details"]["fields"][0]["message"]


# ======================================================================= photos


def _upload(client: TestClient, headers: dict, data: bytes = PNG, purpose: str = "listing_image") -> object:
    return client.post("/api/v1/files", headers=headers, files={"file": ("bread.png", data, "image/png")},
                       data={"purpose": purpose})


def test_listing_photo(client: TestClient, provider: tuple, food: dict, make_user: MakeUser) -> None:
    upload = _upload(client, provider[1])
    assert upload.status_code == 201
    data = _create(client, provider[1], bread(food, image_file_id=upload.json()["id"]))
    assert data["image"]["mime_type"] == "image/png"

    # A food photo is visible to other logged-in users (like the listing)...
    receiver = login_headers(client, make_user(role=UserRole.RECEIVER, verification_status=APPROVED).email)
    assert client.get(f"/api/v1/files/{data['image']['id']}", headers=receiver).status_code == 200
    # ...but verification documents stay private.
    doc = _upload(client, provider[1], PDF, "verification_doc").json()["id"]
    assert client.get(f"/api/v1/files/{doc}", headers=receiver).status_code == 404


def test_photo_rules(client: TestClient, provider: tuple, food: dict, make_user: MakeUser, db_session: Session) -> None:
    assert _upload(client, provider[1], PDF).status_code == 415  # photos must be images
    receiver = login_headers(client, make_user(role=UserRole.RECEIVER).email)
    assert _upload(client, receiver).status_code == 403  # only providers upload food photos

    _, other = _make_provider(client, make_user, db_session)
    someone_elses = _upload(client, other).json()["id"]
    doc = _upload(client, provider[1], PDF, "verification_doc").json()["id"]
    for file_id in (someone_elses, doc):
        response = client.post("/api/v1/listings", headers=provider[1], json=bread(food, image_file_id=file_id))
        assert response.status_code == 422 and _code(response) == "INVALID_FILE"


# ===================================================================== reading


def test_who_can_see_a_listing(
    client: TestClient, provider: tuple, food: dict, make_user: MakeUser, db_session: Session
) -> None:
    listing = _create(client, provider[1], bread(food))
    url = f"/api/v1/listings/{listing['id']}"

    def headers_for(role: UserRole, status: VerificationStatus = APPROVED) -> dict:
        return login_headers(client, make_user(role=role, verification_status=status).email)

    _, other_provider = _make_provider(client, make_user, db_session)
    assert client.get(url, headers=provider[1]).status_code == 200
    assert client.get(url, headers=headers_for(UserRole.ADMIN)).status_code == 200
    verified_receiver = headers_for(UserRole.RECEIVER)
    assert client.get(url, headers=verified_receiver).status_code == 200
    assert client.get(url, headers=headers_for(UserRole.RECEIVER, VerificationStatus.PENDING)).status_code == 404
    assert client.get(url, headers=headers_for(UserRole.VOLUNTEER)).status_code == 404
    assert client.get(url, headers=other_provider).status_code == 404
    assert client.get(url).status_code == 401

    client.post(f"{url}/pause", headers=provider[1])
    assert client.get(url, headers=verified_receiver).status_code == 404  # paused = hidden from receivers
    assert client.get(url, headers=provider[1]).status_code == 200


def test_my_listings_paging_and_filters(client: TestClient, provider: tuple, food: dict) -> None:
    ids = [_create(client, provider[1], bread(food, food_name=f"Bread {n}"))["id"] for n in range(3)]
    client.post(f"/api/v1/listings/{ids[0]}/pause", headers=provider[1])
    client.delete(f"/api/v1/listings/{ids[1]}", headers=provider[1])

    page = client.get("/api/v1/providers/me/listings?page_size=1", headers=provider[1]).json()
    assert page["total"] == 2 and len(page["items"]) == 1  # removed one is hidden
    assert page["items"][0]["food_name"] == "Bread 2"  # newest first
    paused = client.get("/api/v1/providers/me/listings?status=paused", headers=provider[1]).json()
    assert [i["id"] for i in paused["items"]] == [ids[0]]
    removed = client.get("/api/v1/providers/me/listings?status=removed", headers=provider[1]).json()
    assert [i["id"] for i in removed["items"]] == [ids[1]]


# ====================================================================== editing


def test_edit_needs_the_current_version(client: TestClient, provider: tuple, food: dict) -> None:
    listing = _create(client, provider[1], bread(food))
    url = f"/api/v1/listings/{listing['id']}"
    changed = bread(food, food_name="Fresh bread and buns", total_quantity=40)

    missing = client.put(url, headers=provider[1], json=changed)
    assert missing.status_code == 428 and _code(missing) == "VERSION_REQUIRED"

    ok = client.put(url, headers={**provider[1], "If-Match": '"1"'}, json=changed)  # ETag style accepted
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == 2 and ok.json()["total_quantity"] == 40 and ok.headers["etag"] == '"2"'

    # A second device still holding version 1 must not overwrite the change:
    stale = client.put(url, headers={**provider[1], "If-Match": "1"}, json=bread(food, food_name="Old"))
    assert stale.status_code == 409 and _code(stale) == "STALE_VERSION"
    assert stale.json()["error"]["details"]["current_version"] == 2
    assert client.get(url, headers=provider[1]).json()["food_name"] == "Fresh bread and buns"

    bad = client.put(url, headers={**provider[1], "If-Match": "abc"}, json=changed)
    assert bad.status_code == 422


def test_edit_rules(client: TestClient, provider: tuple, food: dict, make_user: MakeUser, db_session: Session) -> None:
    listing = _create(client, provider[1], bread(food))
    url = f"/api/v1/listings/{listing['id']}"
    db_session.execute(update(FoodListing).values(allocated_quantity=20))  # pretend 20 were promised
    db_session.commit()
    low = client.put(url, headers={**provider[1], "If-Match": "1"}, json=bread(food, total_quantity=15))
    assert low.status_code == 422 and _code(low) == "QUANTITY_BELOW_ALLOCATED"

    _, other = _make_provider(client, make_user, db_session)
    assert client.put(url, headers={**other, "If-Match": "1"}, json=bread(food)).status_code == 404

    closed = client.post(f"{url}/close", headers=provider[1]).json()
    response = client.put(url, headers={**provider[1], "If-Match": str(closed["version"])}, json=bread(food))
    assert response.status_code == 409 and _code(response) == "LISTING_NOT_EDITABLE"


def test_two_devices_editing_at_once_only_one_wins(
    client: TestClient, provider: tuple, food: dict, test_db_engine, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    listing = _create(client, provider[1], bread(food))
    user = provider[0]
    original_validate = listing_service._validate

    def slow_validate(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        time.sleep(0.5)  # both requests now overlap between "check version" and "save"
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(listing_service, "_validate", slow_validate)
    barrier, results = threading.Barrier(2), []

    def edit(name: str) -> None:
        with Session(test_db_engine, expire_on_commit=False) as db:
            actor = db.get(User, user.id)
            barrier.wait()
            try:
                listing_service.update(db, actor, listing["id"], 1, ListingIn(**bread(food, food_name=name)))
                results.append("ok")
            except AppError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=edit, args=(n,)) for n in ("Phone edit", "Laptop edit")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(results) == ["STALE_VERSION", "ok"]


# ============================================================== status changes


def test_status_flow_and_history(client: TestClient, provider: tuple, food: dict) -> None:
    listing = _create(client, provider[1], bread(food))
    url = f"/api/v1/listings/{listing['id']}"
    assert client.post(f"{url}/pause", headers=provider[1], json={"reason": "Oven broke"}).json()["status"] == "paused"
    assert client.post(f"{url}/resume", headers=provider[1]).json()["status"] == "active"
    closed = client.post(f"{url}/close", headers=provider[1])
    assert closed.json()["status"] == "closed" and closed.json()["version"] == 4

    reopen = client.post(f"{url}/resume", headers=provider[1])
    assert reopen.status_code == 409 and _code(reopen) == "INVALID_STATUS_TRANSITION"

    history = client.get(f"{url}/history", headers=provider[1]).json()
    assert [(h["from_status"], h["to_status"]) for h in history] == [
        (None, "active"), ("active", "paused"), ("paused", "active"), ("active", "closed")]
    assert history[1]["reason"] == "Oven broke" and history[0]["changed_by"] == str(provider[0].id)


def test_resume_after_deadline_is_refused(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    listing = _create(client, provider[1], bread(food))
    client.post(f"/api/v1/listings/{listing['id']}/pause", headers=provider[1])
    db_session.execute(update(FoodListing).values(
        pickup_window_start=datetime.now(UTC) - timedelta(hours=5),
        pickup_window_end=datetime.now(UTC) - timedelta(hours=2),
        collection_deadline=datetime.now(UTC) - timedelta(hours=1)))
    db_session.commit()
    response = client.post(f"/api/v1/listings/{listing['id']}/resume", headers=provider[1])
    assert response.status_code == 409 and _code(response) == "DEADLINE_PASSED"


def test_remove_is_a_soft_delete(client: TestClient, provider: tuple, food: dict, make_user: MakeUser,
                                 db_session: Session) -> None:
    listing = _create(client, provider[1], bread(food))
    url = f"/api/v1/listings/{listing['id']}"
    assert client.delete(url, headers=provider[1]).status_code == 204
    assert client.get(url, headers=provider[1]).status_code == 404  # gone for the provider
    assert client.delete(url, headers=provider[1]).status_code == 404
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    assert client.get(url, headers=admin).json()["status"] == "removed"  # kept for history and reports
    assert db_session.scalar(select(FoodListing.id)) is not None


def test_history_is_private(client: TestClient, provider: tuple, food: dict, make_user: MakeUser) -> None:
    listing = _create(client, provider[1], bread(food))
    receiver = login_headers(client, make_user(role=UserRole.RECEIVER, verification_status=APPROVED).email)
    assert client.get(f"/api/v1/listings/{listing['id']}/history", headers=receiver).status_code == 404


@pytest.mark.parametrize("current", list(ListingStatus))
def test_state_machine_table(current: ListingStatus) -> None:
    """Every (from, to) pair either is allowed by the table or raises 409."""
    for target in ListingStatus:
        allowed = target in LISTING_PROVIDER_TRANSITIONS[current]
        if allowed:
            check_listing_transition(current, target)
        else:
            with pytest.raises(AppError) as info:
                check_listing_transition(current, target)
            assert info.value.status_code == 409


# ============================================================= database rules


def test_database_refuses_over_allocation(client: TestClient, provider: tuple, food: dict, db_session: Session) -> None:
    _create(client, provider[1], bread(food))  # 30 packages
    with pytest.raises(IntegrityError, match="ck_food_listings_allocated_within_total"):
        db_session.execute(update(FoodListing).values(allocated_quantity=31))
        db_session.commit()
    db_session.rollback()


def test_fuzzy_search_index_exists(db_session: Session) -> None:
    indexdef = db_session.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_food_listings_food_name_trgm'"))
    assert "gin" in indexdef and "gin_trgm_ops" in indexdef
