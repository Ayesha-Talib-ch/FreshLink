"""Provider, receiver and volunteer profiles, food preferences, availability, account update."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory
from app.models.profiles import ReceiverFoodPreference
from tests.conftest import MakeUser, login_headers

LAHORE = {"address": "Main Boulevard, Gulberg III, Lahore", "latitude": 31.5204, "longitude": 74.3587}


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


def _ids(db: Session, model) -> dict[str, str]:  # noqa: ANN001
    return {c.code: str(c.id) for c in db.scalars(select(model))}


@pytest.fixture
def cats(db_session: Session) -> dict[str, dict[str, str]]:
    return {
        "provider": _ids(db_session, ProviderCategory),
        "receiver": _ids(db_session, ReceiverCategory),
        "food": _ids(db_session, FoodCategory),
    }


def _headers(client: TestClient, make_user: MakeUser, role: UserRole, **kwargs) -> dict[str, str]:  # noqa: ANN003
    return login_headers(client, make_user(role=role, **kwargs).email)


@pytest.fixture
def provider(client: TestClient, make_user: MakeUser) -> dict[str, str]:
    # A NOT yet verified provider: profiles must work before approval.
    return _headers(client, make_user, UserRole.PROVIDER, verification_status=VerificationStatus.NOT_SUBMITTED)


@pytest.fixture
def receiver(client: TestClient, make_user: MakeUser) -> dict[str, str]:
    return _headers(client, make_user, UserRole.RECEIVER)


@pytest.fixture
def volunteer(client: TestClient, make_user: MakeUser) -> dict[str, str]:
    return _headers(client, make_user, UserRole.VOLUNTEER)


def _provider_body(cats: dict, **overrides) -> dict:  # noqa: ANN003
    return {
        **LAHORE,
        "business_name": "  Lahore   Fresh Bakers ",
        "provider_category_id": cats["provider"]["bakery"],
        "contact_person": "Ali Raza",
        "contact_phone": "0300-1234567",
        "opening_hours": {"mon": [{"open": "09:00", "close": "13:00"}, {"open": "15:00", "close": "22:00"}]},
        **overrides,
    }


def _receiver_body(cats: dict, code: str = "ngo", **overrides) -> dict:  # noqa: ANN003
    return {**LAHORE, "receiver_name": "Hope Welfare Trust",
            "receiver_category_id": cats["receiver"][code], **overrides}


# ------------------------------------------------------------------ provider


def test_provider_profile_not_found_before_creation(client: TestClient, provider: dict) -> None:
    response = client.get("/api/v1/providers/me", headers=provider)
    assert response.status_code == 404 and _code(response) == "PROFILE_NOT_FOUND"


def test_provider_creates_then_updates_profile(client: TestClient, provider: dict, cats: dict) -> None:
    created = client.put("/api/v1/providers/me", headers=provider, json=_provider_body(cats))
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["business_name"] == "Lahore Fresh Bakers"
    assert data["contact_phone"] == "+923001234567"
    assert data["category"]["code"] == "bakery"
    assert data["opening_hours"]["mon"][1] == {"open": "15:00:00", "close": "22:00:00"}

    updated = client.put("/api/v1/providers/me", headers=provider,
                         json=_provider_body(cats, provider_category_id=cats["provider"]["restaurant"],
                                             opening_hours=None))
    assert updated.status_code == 200
    assert updated.json()["id"] == data["id"]  # same profile, not a second one
    assert updated.json()["category"]["code"] == "restaurant"
    assert client.get("/api/v1/providers/me", headers=provider).json()["opening_hours"] is None


@pytest.mark.parametrize(
    ("overrides", "reported_field"),
    [
        ({"latitude": 91}, "body.latitude"),
        ({"longitude": -181}, "body.longitude"),
        ({"business_name": " x "}, "body.business_name"),
        ({"contact_phone": "123"}, "body.contact_phone"),
        ({"address": "abc"}, "body.address"),
        ({"opening_hours": {"mon": [{"open": "10:00", "close": "09:00"}]}}, "body.opening_hours.mon.0"),
        ({"opening_hours": {"mon": [{"open": "09:00", "close": "12:00"}, {"open": "11:00", "close": "14:00"}]}},
         "body.opening_hours"),
        ({"opening_hours": {"someday": []}}, "body.opening_hours.someday.[key]"),
    ],
)
def test_provider_profile_validation(
    client: TestClient, provider: dict, cats: dict, overrides: dict, reported_field: str
) -> None:
    response = client.put("/api/v1/providers/me", headers=provider, json=_provider_body(cats, **overrides))
    assert response.status_code == 422, response.text
    assert [f["field"] for f in response.json()["error"]["details"]["fields"]] == [reported_field]


def test_provider_unknown_or_inactive_category(
    client: TestClient, provider: dict, cats: dict, db_session: Session
) -> None:
    unknown = client.put("/api/v1/providers/me", headers=provider,
                         json=_provider_body(cats, provider_category_id=str(uuid.uuid4())))
    assert unknown.status_code == 422 and _code(unknown) == "CATEGORY_NOT_AVAILABLE"

    db_session.execute(update(ProviderCategory).where(ProviderCategory.code == "grocery").values(is_active=False))
    db_session.commit()
    try:
        inactive = client.put("/api/v1/providers/me", headers=provider,
                              json=_provider_body(cats, provider_category_id=cats["provider"]["grocery"]))
        assert inactive.status_code == 422 and _code(inactive) == "CATEGORY_NOT_AVAILABLE"
    finally:
        db_session.execute(update(ProviderCategory).where(ProviderCategory.code == "grocery").values(is_active=True))
        db_session.commit()


# --------------------------------------------------------- role separation


@pytest.mark.parametrize(
    ("path", "allowed"),
    [("/api/v1/providers/me", "provider"), ("/api/v1/receivers/me", "receiver"),
     ("/api/v1/receivers/me/food-preferences", "receiver"), ("/api/v1/volunteers/me", "volunteer"),
     ("/api/v1/volunteers/me/availability", "volunteer")],
)
def test_each_profile_is_only_for_its_own_role(
    client: TestClient, make_user: MakeUser, path: str, allowed: str
) -> None:
    assert client.get(path).status_code == 401
    for role in UserRole:
        headers = _headers(client, make_user, role)
        status = client.get(path, headers=headers).status_code
        assert (status == 404) if role.value == allowed else (status == 403), (role, status)


def test_users_cannot_see_each_others_profiles(client: TestClient, make_user: MakeUser, cats: dict) -> None:
    first = _headers(client, make_user, UserRole.PROVIDER)
    second = _headers(client, make_user, UserRole.PROVIDER)
    client.put("/api/v1/providers/me", headers=first, json=_provider_body(cats))
    response = client.get("/api/v1/providers/me", headers=second)
    assert response.status_code == 404  # "me" always means the caller


# ------------------------------------------------------------------ receiver


def test_receiver_ngo_profile(client: TestClient, receiver: dict, cats: dict) -> None:
    response = client.put("/api/v1/receivers/me", headers=receiver,
                          json=_receiver_body(cats, max_pickup_radius_km=7.5, has_own_transport=True,
                                              household_size=4))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["category"]["code"] == "ngo"
    assert data["max_pickup_radius_km"] == 7.5
    assert data["household_size"] is None  # only for individuals: ignored here
    assert data["is_eligible"] is True and data["food_preferences"] == []


def test_individual_needs_household_size(client: TestClient, receiver: dict, cats: dict) -> None:
    missing = client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats, "individual"))
    assert missing.status_code == 422 and _code(missing) == "HOUSEHOLD_SIZE_REQUIRED"
    ok = client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats, "individual", household_size=5))
    assert ok.status_code == 200 and ok.json()["household_size"] == 5


def test_small_business_must_declare_not_for_resale(client: TestClient, receiver: dict, cats: dict) -> None:
    missing = client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats, "small_business"))
    assert missing.status_code == 422 and _code(missing) == "NOT_FOR_RESALE_REQUIRED"
    ok = client.put("/api/v1/receivers/me", headers=receiver,
                    json=_receiver_body(cats, "small_business", not_for_resale_declared=True))
    assert ok.status_code == 200 and ok.json()["not_for_resale_declared"] is True


def test_receiver_cannot_make_themselves_eligible(client: TestClient, receiver: dict, cats: dict, db_session: Session) -> None:
    client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats))
    from app.models.profiles import ReceiverProfile
    db_session.execute(update(ReceiverProfile).values(is_eligible=False))
    db_session.commit()
    response = client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats, is_eligible=True))
    assert response.status_code == 200
    assert response.json()["is_eligible"] is False  # extra field ignored; only admins change it


@pytest.mark.parametrize("radius", [0, -1, 101, 5.25])
def test_receiver_radius_validation(client: TestClient, receiver: dict, cats: dict, radius: float) -> None:
    response = client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats, max_pickup_radius_km=radius))
    assert response.status_code == 422


# ---------------------------------------------------------- food preferences


def test_food_preferences_need_a_profile_first(client: TestClient, receiver: dict, cats: dict) -> None:
    response = client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json={"preferences": []})
    assert response.status_code == 409 and _code(response) == "PROFILE_REQUIRED"


def test_food_preferences_replace_the_whole_list(
    client: TestClient, receiver: dict, cats: dict, db_session: Session
) -> None:
    client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats))
    first = [{"food_category_id": cats["food"]["bakery_items"], "max_quantity_per_pickup": 20, "unit": "package"},
             {"food_category_id": cats["food"]["cooked_meals"], "max_quantity_per_pickup": 12.5, "unit": "kg"}]
    response = client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json={"preferences": first})
    assert response.status_code == 200, response.text
    assert sorted((p["food_category"]["code"], p["max_quantity_per_pickup"], p["unit"]) for p in response.json()) == [
        ("bakery_items", 20.0, "package"), ("cooked_meals", 12.5, "kg")]

    # Second save: bakery kept with a new amount, cooked meals removed, dairy added.
    second = [{"food_category_id": cats["food"]["bakery_items"], "max_quantity_per_pickup": 30, "unit": "package"},
              {"food_category_id": cats["food"]["dairy"], "max_quantity_per_pickup": 10, "unit": "litre"}]
    response = client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json={"preferences": second})
    assert response.status_code == 200, response.text
    codes = sorted(p["food_category"]["code"] for p in response.json())
    assert codes == ["bakery_items", "dairy"]
    assert len(db_session.scalars(select(ReceiverFoodPreference)).all()) == 2  # old rows really deleted

    assert client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json={"preferences": []}).json() == []
    assert client.get("/api/v1/receivers/me/food-preferences", headers=receiver).json() == []


@pytest.mark.parametrize(
    ("prefs", "expected_code"),
    [
        ([{"max_quantity_per_pickup": 2.5, "unit": "tray"}], "VALIDATION_ERROR"),  # trays are whole numbers
        ([{"max_quantity_per_pickup": 0, "unit": "kg"}], "VALIDATION_ERROR"),
        ([{"max_quantity_per_pickup": 5, "unit": "bucket"}], "VALIDATION_ERROR"),
        ([{"max_quantity_per_pickup": 5, "unit": "kg"}, {"max_quantity_per_pickup": 6, "unit": "kg"}],
         "VALIDATION_ERROR"),  # the same category twice
    ],
)
def test_food_preference_validation(
    client: TestClient, receiver: dict, cats: dict, prefs: list, expected_code: str
) -> None:
    client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats))
    body = {"preferences": [{"food_category_id": cats["food"]["bakery_items"], **p} for p in prefs]}
    response = client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json=body)
    assert response.status_code == 422 and _code(response) == expected_code


def test_food_preference_unknown_category_keeps_old_list(
    client: TestClient, receiver: dict, cats: dict
) -> None:
    client.put("/api/v1/receivers/me", headers=receiver, json=_receiver_body(cats))
    good = [{"food_category_id": cats["food"]["dairy"], "max_quantity_per_pickup": 10, "unit": "litre"}]
    client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json={"preferences": good})
    bad = good + [{"food_category_id": str(uuid.uuid4()), "max_quantity_per_pickup": 1, "unit": "kg"}]
    response = client.put("/api/v1/receivers/me/food-preferences", headers=receiver, json={"preferences": bad})
    assert response.status_code == 422 and _code(response) == "CATEGORY_NOT_AVAILABLE"
    assert len(client.get("/api/v1/receivers/me/food-preferences", headers=receiver).json()) == 1


# ----------------------------------------------------------------- volunteer


def test_volunteer_profile_and_availability(client: TestClient, volunteer: dict) -> None:
    body = {**LAHORE, "has_vehicle": True, "vehicle_type": "motorbike", "service_radius_km": 8, "is_available_now": True}
    response = client.put("/api/v1/volunteers/me", headers=volunteer, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["vehicle_type"] == "motorbike" and response.json()["availability"] == []

    slots = [{"day_of_week": 4, "start_time": "17:00", "end_time": "21:00"},
             {"day_of_week": 0, "start_time": "09:00", "end_time": "12:00"},
             {"day_of_week": 0, "start_time": "12:00", "end_time": "14:00"}]  # touching, not overlapping
    response = client.put("/api/v1/volunteers/me/availability", headers=volunteer, json={"slots": slots})
    assert response.status_code == 200, response.text
    assert [(s["day_of_week"], s["start_time"]) for s in response.json()] == [
        (0, "09:00:00"), (0, "12:00:00"), (4, "17:00:00")]  # returned in week order

    response = client.put("/api/v1/volunteers/me/availability", headers=volunteer,
                          json={"slots": [{"day_of_week": 6, "start_time": "10:00", "end_time": "11:00"}]})
    assert len(response.json()) == 1
    assert len(client.get("/api/v1/volunteers/me", headers=volunteer).json()["availability"]) == 1


@pytest.mark.parametrize(
    "slots",
    [
        [{"day_of_week": 7, "start_time": "09:00", "end_time": "10:00"}],
        [{"day_of_week": 1, "start_time": "10:00", "end_time": "09:00"}],
        [{"day_of_week": 1, "start_time": "09:00", "end_time": "12:00"},
         {"day_of_week": 1, "start_time": "11:00", "end_time": "13:00"}],
    ],
)
def test_availability_validation(client: TestClient, volunteer: dict, slots: list) -> None:
    client.put("/api/v1/volunteers/me", headers=volunteer, json=LAHORE)
    response = client.put("/api/v1/volunteers/me/availability", headers=volunteer, json={"slots": slots})
    assert response.status_code == 422


def test_vehicle_type_requires_a_vehicle(client: TestClient, volunteer: dict) -> None:
    response = client.put("/api/v1/volunteers/me", headers=volunteer,
                          json={**LAHORE, "has_vehicle": False, "vehicle_type": "car"})
    assert response.status_code == 422


def test_availability_needs_a_profile_first(client: TestClient, volunteer: dict) -> None:
    response = client.put("/api/v1/volunteers/me/availability", headers=volunteer, json={"slots": []})
    assert response.status_code == 409 and _code(response) == "PROFILE_REQUIRED"


# ------------------------------------------------------------------- account


def test_update_own_account(client: TestClient, make_user: MakeUser) -> None:
    headers = _headers(client, make_user, UserRole.RECEIVER)
    response = client.put("/api/v1/users/me", headers=headers, json={"full_name": " Sara  Khan ", "phone": "0333 9990001"})
    assert response.status_code == 200
    assert response.json()["full_name"] == "Sara Khan" and response.json()["phone"] == "+923339990001"
    assert client.get("/api/v1/users/me", headers=headers).json()["phone"] == "+923339990001"


def test_cannot_take_someone_elses_phone(client: TestClient, make_user: MakeUser) -> None:
    make_user(phone="+923331112222")
    headers = _headers(client, make_user, UserRole.RECEIVER)
    response = client.put("/api/v1/users/me", headers=headers, json={"full_name": "Sara Khan", "phone": "0333-1112222"})
    assert response.status_code == 409 and _code(response) == "PHONE_ALREADY_REGISTERED"


def test_keeping_own_phone_is_fine(client: TestClient, make_user: MakeUser) -> None:
    user = make_user(role=UserRole.VOLUNTEER)
    headers = login_headers(client, user.email)
    response = client.put("/api/v1/users/me", headers=headers, json={"full_name": "New Name", "phone": user.phone})
    assert response.status_code == 200
