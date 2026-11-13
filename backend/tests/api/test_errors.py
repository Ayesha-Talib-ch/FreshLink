"""Every error must use the same JSON shape: {"error": {"code", "message", "details"}}.

Small test-only routes are added to a test app so we can trigger each kind of error.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app


class QuantityIn(BaseModel):
    quantity: int = Field(gt=0)


def _app_with_test_routes(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/test/app-error")
    def raise_app_error() -> None:
        raise AppError(409, "LISTING_QUANTITY_EXCEEDED", "Only 5 packages remain", {"remaining": 5})

    @app.post("/test/validate")
    def validate(body: QuantityIn) -> dict[str, int]:
        return {"quantity": body.quantity}

    @app.get("/test/crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail: password=abc")

    @app.get("/test/bad-sql")
    def bad_sql(db: DbSession) -> None:
        db.execute(text("SELECT * FROM table_that_does_not_exist"))

    return app


def _client(settings: Settings) -> TestClient:
    # raise_server_exceptions=False: behave like the real server for crashes (return a 500).
    return TestClient(_app_with_test_routes(settings), raise_server_exceptions=False)


def _assert_envelope(body: dict, code: str) -> dict:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]


def test_404_uses_error_format(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    _assert_envelope(response.json(), "NOT_FOUND")


def test_405_uses_error_format_and_keeps_allow_header(client: TestClient) -> None:
    response = client.post("/api/v1/version")
    assert response.status_code == 405
    _assert_envelope(response.json(), "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


def test_app_error_returns_its_status_code_and_details(test_settings: Settings) -> None:
    response = _client(test_settings).get("/test/app-error")
    assert response.status_code == 409
    error = _assert_envelope(response.json(), "LISTING_QUANTITY_EXCEEDED")
    assert error["message"] == "Only 5 packages remain"
    assert error["details"] == {"remaining": 5}


def test_validation_error_lists_each_bad_field(test_settings: Settings) -> None:
    response = _client(test_settings).post("/test/validate", json={"quantity": -3})
    assert response.status_code == 422
    error = _assert_envelope(response.json(), "VALIDATION_ERROR")
    fields = error["details"]["fields"]
    assert fields[0]["field"] == "body.quantity"
    assert "greater than 0" in fields[0]["message"]


def test_missing_body_is_a_validation_error(test_settings: Settings) -> None:
    response = _client(test_settings).post("/test/validate")
    assert response.status_code == 422
    _assert_envelope(response.json(), "VALIDATION_ERROR")


def test_unexpected_crash_returns_500_without_leaking_details(test_settings: Settings) -> None:
    response = _client(test_settings).get("/test/crash")
    assert response.status_code == 500
    error = _assert_envelope(response.json(), "INTERNAL_ERROR")
    assert "password" not in response.text
    assert "RuntimeError" not in response.text
    assert error["details"] == {}


def test_database_error_returns_500_without_leaking_sql(test_settings: Settings) -> None:
    response = _client(test_settings).get("/test/bad-sql")
    assert response.status_code == 500
    _assert_envelope(response.json(), "INTERNAL_ERROR")
    assert "table_that_does_not_exist" not in response.text
