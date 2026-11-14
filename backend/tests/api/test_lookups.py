"""Public lookup endpoints and the seeded categories."""

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.category import FoodCategory


def test_provider_categories_match_the_spec(client: TestClient) -> None:
    response = client.get("/api/v1/lookups/provider-categories")
    assert response.status_code == 200
    assert [c["code"] for c in response.json()] == ["bakery", "restaurant", "fast_food", "grocery"]


def test_receiver_categories_match_the_spec(client: TestClient) -> None:
    data = client.get("/api/v1/lookups/receiver-categories").json()
    assert [c["code"] for c in data] == [
        "ngo", "community_kitchen", "community_group", "individual", "small_business"
    ]
    assert all(c["requires_documents"] for c in data)
    assert "priority_weight" not in data[0]  # internal setting, not shown publicly


def test_food_categories_carry_their_date_rules(client: TestClient) -> None:
    data = {c["code"]: c for c in client.get("/api/v1/lookups/food-categories").json()}
    assert data["cooked_meals"]["requires_preparation_time"] is True
    assert data["dairy"]["requires_expiry_date"] is True
    assert data["bakery_items"]["perishability_class"] == "bakery"


def test_lookups_need_no_login(client: TestClient) -> None:
    # The registration form uses them before the user has an account.
    for path in ("provider-categories", "receiver-categories", "food-categories", "enums"):
        assert client.get(f"/api/v1/lookups/{path}").status_code == 200


def test_inactive_categories_are_hidden(client: TestClient, db_session: Session) -> None:
    db_session.execute(update(FoodCategory).where(FoodCategory.code == "other").values(is_active=False))
    db_session.commit()
    try:
        codes = [c["code"] for c in client.get("/api/v1/lookups/food-categories").json()]
        assert "other" not in codes and "dairy" in codes
    finally:  # seeded tables are shared between tests: undo the change
        db_session.execute(update(FoodCategory).where(FoodCategory.code == "other").values(is_active=True))
        db_session.commit()


def test_enums(client: TestClient) -> None:
    data = client.get("/api/v1/lookups/enums").json()
    assert data["public_roles"] == ["provider", "receiver", "volunteer"]  # no "admin"
    assert data["quantity_units"] == ["kg", "litre", "piece", "package", "portion", "tray"]
    assert data["countable_units"] == ["package", "piece", "portion", "tray"]
    assert data["days_of_week"][0] == "mon"
