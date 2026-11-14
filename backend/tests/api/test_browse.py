"""Stage 4a: receivers browse / search / filter available food."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import literal, select, update
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.engines.geo import haversine_km, haversine_km_sql
from app.models.category import FoodCategory, ReceiverCategory
from app.models.listing import FoodListing
from tests.api.test_listings import _create, _make_provider, at, biryani, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
GULBERG = (31.5204, 74.3587)  # the receiver's saved location
PLACES = {  # pickup points around Lahore
    "Gulberg": (31.5210, 74.3590),
    "Model Town": (31.4800, 74.3200),
    "DHA": (31.4700, 74.4000),
    "Raiwind": (31.2500, 74.2100),
    "Karachi": (24.8607, 67.0011),
}
URL = "/api/v1/listings"


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser, db_session: Session) -> tuple:
    return _make_provider(client, make_user, db_session)


def _receiver(client: TestClient, make_user: MakeUser, db: Session, *, profile: bool = True,
              verified: bool = True, radius: float = 10) -> dict:
    user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED if verified else VerificationStatus.PENDING)
    headers = login_headers(client, user.email)
    if profile:
        category = db.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == "ngo"))
        body = {"receiver_name": "Hope Trust", "receiver_category_id": str(category), "address": "Gulberg, Lahore",
                "latitude": GULBERG[0], "longitude": GULBERG[1], "max_pickup_radius_km": radius}
        assert client.put("/api/v1/receivers/me", headers=headers, json=body).status_code == 200
    return headers


@pytest.fixture
def receiver(client: TestClient, make_user: MakeUser, db_session: Session) -> dict:
    return _receiver(client, make_user, db_session)


def at_place(body: dict, place: str) -> dict:
    lat, lng = PLACES[place]
    return {**body, "pickup_address": f"{place}, Lahore", "pickup_latitude": lat, "pickup_longitude": lng}


def _names(response) -> list[str]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    return [item["food_name"] for item in response.json()["items"]]


@pytest.fixture
def around_lahore(client: TestClient, provider: tuple, food: dict) -> dict[str, dict]:
    return {place: _create(client, provider[1], at_place(bread(food, food_name=f"Bread {place}"), place))
            for place in PLACES}


# =============================================================== location


def test_uses_saved_location_and_pickup_radius(client: TestClient, receiver: dict, around_lahore: dict) -> None:
    response = client.get(f"{URL}?sort=distance", headers=receiver)
    assert _names(response) == ["Bread Gulberg", "Bread Model Town", "Bread DHA"]  # nearest first, within 10 km
    data = response.json()
    assert data["location"] == {"latitude": GULBERG[0], "longitude": GULBERG[1], "radius_km": 10.0, "source": "profile"}
    for item in data["items"]:
        place = item["food_name"].removeprefix("Bread ")
        assert item["distance_km"] == pytest.approx(haversine_km(*GULBERG, *PLACES[place]), abs=0.01)


def test_wider_radius_and_another_location(client: TestClient, receiver: dict, around_lahore: dict) -> None:
    assert "Bread Raiwind" in _names(client.get(f"{URL}?radius_km=50", headers=receiver))
    near_karachi = client.get(f"{URL}?near_lat=24.86&near_lng=67.0&radius_km=20", headers=receiver)
    assert _names(near_karachi) == ["Bread Karachi"]
    assert near_karachi.json()["location"]["source"] == "request"


def test_receiver_without_profile_sees_all_without_distance(
    client: TestClient, make_user: MakeUser, db_session: Session, around_lahore: dict
) -> None:
    headers = _receiver(client, make_user, db_session, profile=False)
    data = client.get(URL, headers=headers).json()
    assert data["total"] == 5 and data["location"] is None
    assert all(item["distance_km"] is None for item in data["items"])
    for query in ("sort=distance", "radius_km=5"):
        response = client.get(f"{URL}?{query}", headers=headers)
        assert response.status_code == 422 and response.json()["error"]["code"] == "LOCATION_REQUIRED"


def test_half_a_location_is_rejected(client: TestClient, receiver: dict) -> None:
    response = client.get(f"{URL}?near_lat=31.5", headers=receiver)
    assert response.status_code == 422 and response.json()["error"]["code"] == "LOCATION_INCOMPLETE"


# ========================================================== what is shown


def test_only_food_that_can_still_be_collected(
    client: TestClient, receiver: dict, provider: tuple, food: dict, db_session: Session
) -> None:
    def make(name: str, body: dict | None = None) -> str:
        return _create(client, provider[1], at_place(body or bread(food, food_name=name), "Gulberg"))["id"]

    visible = make("Visible")
    partly = make("Partly allocated")
    paused = make("Paused")
    closed = make("Closed")
    removed = make("Removed")
    deadline_passed = make("Deadline passed")
    expired_date = make("Expiry passed", bread(food, food_name="Expiry passed", expiry_at=at(5)))
    gone = make("Fully allocated")
    old_cooking = make("Cooked 13 h ago", biryani(food, food_name="Cooked 13 h ago", preparation_at=at(-1)))

    client.post(f"{URL}/{paused}/pause", headers=provider[1])
    client.post(f"{URL}/{closed}/close", headers=provider[1])
    client.delete(f"{URL}/{removed}", headers=provider[1])
    past = datetime.now(UTC) - timedelta(minutes=1)
    db_session.execute(update(FoodListing).where(FoodListing.id == deadline_passed).values(
        pickup_window_start=past - timedelta(hours=2), pickup_window_end=past, collection_deadline=past))
    db_session.execute(update(FoodListing).where(FoodListing.id == expired_date).values(expiry_at=past))
    db_session.execute(update(FoodListing).where(FoodListing.id == gone).values(allocated_quantity=30))
    db_session.execute(update(FoodListing).where(FoodListing.id == partly).values(allocated_quantity=10))
    # Cooked meals: placeholder limit of 12 h after preparation
    db_session.execute(update(FoodListing).where(FoodListing.id == old_cooking).values(
        preparation_at=datetime.now(UTC) - timedelta(hours=13)))
    db_session.commit()

    response = client.get(URL, headers=receiver)
    assert sorted(_names(response)) == ["Partly allocated", "Visible"]
    partial = next(i for i in response.json()["items"] if i["id"] == partly)
    assert partial["remaining_quantity"] == 20
    assert {visible, partly} == {i["id"] for i in response.json()["items"]}


# ============================================================== filters


def test_search_by_name_tolerates_typos(client: TestClient, receiver: dict, provider: tuple, food: dict) -> None:
    _create(client, provider[1], at_place(biryani(food), "Gulberg"))
    _create(client, provider[1], at_place(bread(food, food_name="Naan and roti"), "Gulberg"))
    assert _names(client.get(f"{URL}?q=biryni", headers=receiver)) == ["Chicken biryani"]  # misspelt
    assert _names(client.get(f"{URL}?q=ROTI", headers=receiver)) == ["Naan and roti"]  # any case
    assert _names(client.get(f"{URL}?q=%25", headers=receiver)) == []  # "%" is not a wildcard
    assert _names(client.get(f"{URL}?q=pizza", headers=receiver)) == []


def test_category_unit_and_quantity_filters(
    client: TestClient, receiver: dict, provider: tuple, food: dict, db_session: Session
) -> None:
    big = _create(client, provider[1], at_place(bread(food, food_name="Big", total_quantity=50), "Gulberg"))
    _create(client, provider[1], at_place(bread(food, food_name="Small", total_quantity=5), "Gulberg"))
    _create(client, provider[1], at_place(biryani(food, food_name="Rice", total_quantity=40), "Gulberg"))

    assert _names(client.get(f"{URL}?food_category_id={food['cooked_meals']}", headers=receiver)) == ["Rice"]
    assert sorted(_names(client.get(f"{URL}?unit=package&min_quantity=5", headers=receiver))) == ["Big", "Small"]
    assert _names(client.get(f"{URL}?unit=package&min_quantity=10", headers=receiver)) == ["Big"]

    db_session.execute(update(FoodListing).where(FoodListing.id == big["id"]).values(allocated_quantity=45))
    db_session.commit()  # only 5 remain now
    assert _names(client.get(f"{URL}?unit=package&min_quantity=10", headers=receiver)) == []

    no_unit = client.get(f"{URL}?min_quantity=10", headers=receiver)
    assert no_unit.status_code == 422 and no_unit.json()["error"]["code"] == "UNIT_REQUIRED"


def test_pickup_time_filter(client: TestClient, receiver: dict, provider: tuple, food: dict) -> None:
    _create(client, provider[1], at_place(bread(food, food_name="This afternoon",
                                                 pickup_window_start=at(1), pickup_window_end=at(3)), "Gulberg"))
    _create(client, provider[1], at_place(bread(food, food_name="Tomorrow",
                                                 pickup_window_start=at(24), pickup_window_end=at(26)), "Gulberg"))

    def window(start: float, end: float) -> list[str]:
        params = {"pickup_from": at(start), "pickup_to": at(end)}
        return _names(client.get(URL, headers=receiver, params=params))

    assert window(2, 4) == ["This afternoon"]  # overlaps
    assert window(20, 30) == ["Tomorrow"]
    assert window(5, 10) == []
    bad = client.get(URL, headers=receiver, params={"pickup_from": at(5), "pickup_to": at(1)})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_TIME_RANGE"
    naive = client.get(URL, headers=receiver, params={"pickup_from": "2026-12-01T10:00:00"})
    assert naive.status_code == 422


def test_urgency_filter_and_default_order(client: TestClient, receiver: dict, provider: tuple, food: dict) -> None:
    # Bakery placeholder rule: critical <= 6 h, high <= 24 h, medium <= 48 h
    _create(client, provider[1], at_place(bread(food, food_name="Normal", pickup_window_start=at(60),
                                                 pickup_window_end=at(70)), "Gulberg"))
    _create(client, provider[1], at_place(bread(food, food_name="High", pickup_window_start=at(10),
                                                 pickup_window_end=at(20)), "Gulberg"))
    _create(client, provider[1], at_place(bread(food, food_name="Critical"), "Gulberg"))

    assert _names(client.get(URL, headers=receiver)) == ["Critical", "High", "Normal"]  # most urgent first
    assert _names(client.get(f"{URL}?urgency=critical", headers=receiver)) == ["Critical"]
    assert sorted(_names(client.get(f"{URL}?urgency=critical&urgency=high", headers=receiver))) == ["Critical", "High"]
    assert _names(client.get(f"{URL}?sort=newest", headers=receiver)) == ["Critical", "High", "Normal"]
    assert _names(client.get(f"{URL}?sort=deadline", headers=receiver)) == ["Critical", "High", "Normal"]
    assert client.get(f"{URL}?sort=cheapest", headers=receiver).status_code == 422


def test_paging_never_repeats_or_skips(client: TestClient, receiver: dict, provider: tuple, food: dict) -> None:
    ids = {_create(client, provider[1], at_place(bread(food, food_name=f"B{n}"), "Gulberg"))["id"] for n in range(5)}
    seen: list[str] = []
    for page in (1, 2, 3):
        data = client.get(f"{URL}?page={page}&page_size=2", headers=receiver).json()
        assert data["total"] == 5
        seen += [item["id"] for item in data["items"]]
    assert len(seen) == 5 and set(seen) == ids


# ============================================================= who may browse


def test_who_may_browse(client: TestClient, make_user: MakeUser, db_session: Session, provider: tuple,
                        around_lahore: dict) -> None:
    assert client.get(URL).status_code == 401
    unverified = _receiver(client, make_user, db_session, verified=False)
    response = client.get(URL, headers=unverified)
    assert response.status_code == 403 and response.json()["error"]["code"] == "VERIFICATION_REQUIRED"
    assert client.get(URL, headers=provider[1]).status_code == 403
    volunteer = login_headers(client, make_user(role=UserRole.VOLUNTEER, verification_status=APPROVED).email)
    assert client.get(URL, headers=volunteer).status_code == 403
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    assert client.get(URL, headers=admin).json()["total"] == 5


def test_results_never_show_provider_contact_details(client: TestClient, receiver: dict, around_lahore: dict) -> None:
    item = client.get(URL, headers=receiver).json()["items"][0]
    assert set(item["provider"]) == {"id", "business_name"}


# ================================================================= geo engine


@pytest.mark.parametrize(
    ("a", "b", "km"),
    [(GULBERG, PLACES["Karachi"], 1033), (GULBERG, GULBERG, 0), ((0, 0), (0, 1), 111.19), ((0, 0), (1, 0), 111.19)],
)
def test_haversine_known_distances(a: tuple, b: tuple, km: float) -> None:
    assert haversine_km(*a, *b) == pytest.approx(km, abs=1)
    assert haversine_km(*a, *b) == pytest.approx(haversine_km(*b, *a))  # same both ways


def test_sql_and_python_formulas_agree(db_session: Session) -> None:
    for place, (lat, lng) in PLACES.items():
        sql = db_session.scalar(select(haversine_km_sql(literal(lat), literal(lng), *GULBERG)))
        assert sql == pytest.approx(haversine_km(lat, lng, *GULBERG), abs=1e-6), place
