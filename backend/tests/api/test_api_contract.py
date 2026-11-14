"""The API documentation (OpenAPI) must describe what the API really returns.

The website and the mobile app will use a TypeScript client GENERATED from this
document (Technical Plan section 2.2), so a wrong document means wrong app code.
"""

from fastapi.testclient import TestClient


def _operations(schema: dict) -> list[tuple[str, str, dict]]:
    return [(method.upper(), path, op) for path, methods in schema["paths"].items() for method, op in methods.items()]


def test_every_422_uses_our_error_format(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    for method, path, op in _operations(schema):
        if "422" in op["responses"]:
            ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
            assert ref == "#/components/schemas/ErrorResponse", (method, path)
    assert "HTTPValidationError" not in schema["components"]["schemas"]


def test_error_response_schema_matches_real_errors(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()["components"]["schemas"]
    assert schema["ErrorResponse"]["required"] == ["error"]
    assert set(schema["ErrorBody"]["required"]) == {"code", "message"}
    real = client.post("/api/v1/auth/register", json={}).json()
    assert set(real) == {"error"} and set(real["error"]) == {"code", "message", "details"}


def test_protected_endpoints_document_401_and_403(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    protected = [(m, p, op) for m, p, op in _operations(schema) if op.get("security")]
    assert len(protected) >= 15  # every /me endpoint, logout-all, change-password ...
    for method, path, op in protected:
        assert {"401", "403"} <= set(op["responses"]), (method, path)


def test_public_endpoints_have_no_security(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    for path in ("/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/lookups/food-categories", "/api/v1/health"):
        for op in schema["paths"][path].values():
            assert "security" not in op, path


def test_operation_ids_are_unique_and_readable(client: TestClient) -> None:
    ids = [op["operationId"] for *_, op in _operations(client.get("/api/v1/openapi.json").json())]
    assert len(ids) == len(set(ids))
    assert "auth-login" in ids and "profiles-save_receiver_profile" in ids
    assert all("api_v1" not in i for i in ids)


def test_documented_routes_match_real_routes(client: TestClient) -> None:
    """Every documented endpoint exists (a call never returns 404/405 for the route itself)."""
    for method, path, _ in _operations(client.get("/api/v1/openapi.json").json()):
        response = client.request(method, path)
        assert response.status_code not in (404, 405) or response.json()["error"]["code"] == "PROFILE_NOT_FOUND", (
            method, path, response.status_code)
