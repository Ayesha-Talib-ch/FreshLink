"""Stage 5b: matching endpoints, stored snapshots, incoming-request scores, recommendations."""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import UserRole, VerificationStatus
from app.engines.geo import haversine_km
from app.models.category import FoodCategory, ReceiverCategory
from app.models.matching import MatchRecommendation
from app.models.profiles import ReceiverProfile
from app.models.user import User
from tests.api.test_listings import _create, _make_provider, bread
from tests.conftest import MakeUser, login_headers

APPROVED = VerificationStatus.APPROVED
GULBERG_PICKUP = {"pickup_address": "Gulberg, Lahore", "pickup_latitude": 31.521, "pickup_longitude": 74.359}


def _code(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


@pytest.fixture
def food(db_session: Session) -> dict[str, str]:
    return {c.code: str(c.id) for c in db_session.scalars(select(FoodCategory))}


def _receiver(client: TestClient, make_user: MakeUser, db: Session, food: dict, name: str, *, lat: float, lng: float,
              accepts: str | None = "bakery_items", capacity: int = 20, transport: bool = False,
              verified: bool = True, code: str = "ngo") -> tuple[User, dict]:
    user = make_user(role=UserRole.RECEIVER, verification_status=APPROVED if verified else VerificationStatus.PENDING)
    headers = login_headers(client, user.email)
    category = db.scalar(select(ReceiverCategory.id).where(ReceiverCategory.code == code))
    body = {"receiver_name": name, "receiver_category_id": str(category), "address": f"{name} address",
            "latitude": lat, "longitude": lng, "max_pickup_radius_km": 10, "has_own_transport": transport}
    assert client.put("/api/v1/receivers/me", headers=headers, json=body).status_code == 200
    if accepts:
        prefs = [{"food_category_id": food[accepts], "max_quantity_per_pickup": capacity,
                  "unit": "package" if accepts == "bakery_items" else "litre"}]
        assert client.put("/api/v1/receivers/me/food-preferences", headers=headers,
                          json={"preferences": prefs}).status_code == 200
    return user, headers


@pytest.fixture
def world(client: TestClient, make_user: MakeUser, db_session: Session, food: dict) -> dict:
    """A bakery in Gulberg with 30 packages, and six receivers testing every rule."""
    people = {
        "A": _receiver(client, make_user, db_session, food, "A next door", lat=31.522, lng=74.360, capacity=20),
        "B": _receiver(client, make_user, db_session, food, "B kitchen", lat=31.48, lng=74.32, capacity=10,
                       code="community_kitchen"),
        "C": _receiver(client, make_user, db_session, food, "C unverified", lat=31.52, lng=74.36, verified=False),
        "D": _receiver(client, make_user, db_session, food, "D dairy only", lat=31.52, lng=74.36, accepts="dairy"),
        "E": _receiver(client, make_user, db_session, food, "E far", lat=31.25, lng=74.21),
        "F": _receiver(client, make_user, db_session, food, "F far with van", lat=31.25, lng=74.21, transport=True),
    }
    _, provider = _make_provider(client, make_user, db_session)
    listing = _create(client, provider, {**bread(food, total_quantity=30), **GULBERG_PICKUP})
    return {"people": people, "provider": provider, "listing": listing}


def _matches(client: TestClient, w: dict) -> dict:
    response = client.get(f"/api/v1/listings/{w['listing']['id']}/matches", headers=w["provider"])
    assert response.status_code == 200, response.text
    return response.json()


# ================================================================== listing matches


def test_ranked_matches_and_explained_exclusions(client: TestClient, world: dict) -> None:
    data = _matches(client, world)
    assert data["model_version"] == "heuristic-v1" and sum(data["weights"].values()) == pytest.approx(1)
    names = [m["receiver"]["receiver_name"] for m in data["matches"]]
    assert names[0] == "A next door" and set(names) == {"A next door", "B kitchen", "F far with van"}
    top = data["matches"][0]
    assert top["rank"] == 1 and 0 < top["score"] <= 1
    assert set(top["components"]) == {"distance", "capacity_fit", "collection_fit", "urgency_feasibility",
                                      "category_priority", "fairness"}
    expected_km = haversine_km(31.522, 74.360, GULBERG_PICKUP["pickup_latitude"], GULBERG_PICKUP["pickup_longitude"])
    assert top["reasons"][:3] == [f"{expected_km:.1f} km away", "accepts Bakery items", "can take up to 20 packages"]
    assert set(top["receiver"]) == {"id", "receiver_name", "category_name", "distance_km", "has_own_transport"}

    excluded = {e["receiver"]["receiver_name"]: e["reason"] for e in data["excluded"]}
    assert excluded["C unverified"] == "Account not verified, not active, or not eligible."
    assert excluded["D dairy only"].startswith("Doesn't accept Bakery items")
    assert "outside their 10 km pickup radius" in excluded["E far"]
    van = next(m for m in data["matches"] if m["receiver"]["receiver_name"] == "F far with van")
    assert "has own transport" in van["reasons"]


def test_who_may_see_matches(client: TestClient, world: dict, make_user: MakeUser, db_session: Session) -> None:
    url = f"/api/v1/listings/{world['listing']['id']}/matches"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=world["people"]["A"][1]).status_code == 403
    _, other = _make_provider(client, make_user, db_session)
    assert client.get(url, headers=other).status_code == 404
    admin = login_headers(client, make_user(role=UserRole.ADMIN, verification_status=APPROVED).email)
    assert client.get(url, headers=admin).status_code == 200


# =================================================================== stored snapshots


def test_snapshot_stored_on_publish_and_outcome_recorded(client: TestClient, world: dict, db_session: Session) -> None:
    rows = db_session.scalars(select(MatchRecommendation)).all()
    assert len(rows) == 3 and {r.outcome for r in rows} == {"none"}
    assert all(r.model_version == "heuristic-v1" and r.reasons for r in rows)

    a_user, a_headers = world["people"]["A"]
    client.post("/api/v1/requests", headers=a_headers,
                json={"listing_id": world["listing"]["id"], "requested_quantity": 10})
    a_profile = db_session.scalar(select(ReceiverProfile.id).where(ReceiverProfile.user_id == a_user.id))
    db_session.expire_all()
    a_row = db_session.scalar(select(MatchRecommendation).where(MatchRecommendation.receiver_id == a_profile))
    assert a_row.outcome == "requested"  # a label for evaluating / training the ranker later

    refreshed = client.post(f"/api/v1/listings/{world['listing']['id']}/matches/refresh", headers=world["provider"])
    assert refreshed.status_code == 200 and refreshed.json()["stored"] == 3
    db_session.expire_all()
    assert db_session.get(MatchRecommendation, a_row.id).outcome == "requested"  # kept after refresh


# ============================================================ allocation and fairness


def test_allocated_receivers_drop_out_and_fairness_reacts(client: TestClient, world: dict) -> None:
    a_headers = world["people"]["A"][1]
    request_id = client.post("/api/v1/requests", headers=a_headers,
                             json={"listing_id": world["listing"]["id"], "requested_quantity": 10}).json()["id"]
    assert client.post(f"/api/v1/requests/{request_id}/approve", headers=world["provider"]).status_code == 200

    data = _matches(client, world)
    excluded = {e["receiver"]["receiver_name"]: e["reason"] for e in data["excluded"]}
    assert excluded["A next door"] == "Already allocated food from this listing."


# ===================================================== incoming requests with scores


def test_incoming_requests_show_match_scores(client: TestClient, world: dict, db_session: Session) -> None:
    listing_id = world["listing"]["id"]
    for key in ("B", "A"):  # B asks first, A second
        client.post("/api/v1/requests", headers=world["people"][key][1],
                    json={"listing_id": listing_id, "requested_quantity": 5})
        time.sleep(0.01)
    url = f"/api/v1/listings/{listing_id}/requests"

    first_come = client.get(url, headers=world["provider"]).json()
    assert [r["receiver"]["receiver_name"] for r in first_come] == ["B kitchen", "A next door"]
    assert all(r["match"]["score"] is not None and r["match"]["reasons"] for r in first_come)

    by_match = client.get(f"{url}?sort=match", headers=world["provider"]).json()
    assert [r["receiver"]["receiver_name"] for r in by_match] == ["A next door", "B kitchen"]

    # A receiver who becomes ineligible after requesting is shown last, with the reason.
    a_user = world["people"]["A"][0]
    db_session.execute(update(ReceiverProfile).where(ReceiverProfile.user_id == a_user.id).values(is_eligible=False))
    db_session.commit()
    rows = client.get(f"{url}?sort=match", headers=world["provider"]).json()
    assert rows[-1]["receiver"]["receiver_name"] == "A next door"
    assert rows[-1]["match"]["score"] is None and "not eligible" in rows[-1]["match"]["excluded_reason"]

    # The receiver's own view of a request carries no match data.
    mine = client.get("/api/v1/requests", headers=world["people"]["B"][1]).json()["items"][0]
    assert mine["match"] is None


# ================================================================ recommended for you


def test_recommended_for_you(client: TestClient, world: dict) -> None:
    url = "/api/v1/listings/recommended"
    a_headers = world["people"]["A"][1]
    recommended = client.get(url, headers=a_headers)
    assert recommended.status_code == 200, recommended.text  # not mistaken for /listings/{listing_id}
    items = recommended.json()
    assert [i["id"] for i in items] == [world["listing"]["id"]]
    assert items[0]["match"]["score"] > 0 and "accepts Bakery items" in items[0]["match"]["reasons"]

    assert client.get(url, headers=world["people"]["D"][1]).json() == []  # doesn't accept bakery items
    assert client.get(url, headers=world["people"]["E"][1]).json() == []  # too far, no transport
    assert len(client.get(url, headers=world["people"]["F"][1]).json()) == 1  # far, but has a van

    client.post("/api/v1/requests", headers=a_headers, json={"listing_id": world["listing"]["id"],
                                                             "requested_quantity": 5})
    assert client.get(url, headers=a_headers).json() == []  # already requested: not recommended again


def test_recommended_access_rules(client: TestClient, world: dict, make_user: MakeUser) -> None:
    url = "/api/v1/listings/recommended"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=world["provider"]).status_code == 403
    response = client.get(url, headers=world["people"]["C"][1])
    assert response.status_code == 403 and _code(response) == "VERIFICATION_REQUIRED"
    no_profile = login_headers(client, make_user(role=UserRole.RECEIVER, verification_status=APPROVED).email)
    response = client.get(url, headers=no_profile)
    assert response.status_code == 409 and _code(response) == "PROFILE_REQUIRED"
