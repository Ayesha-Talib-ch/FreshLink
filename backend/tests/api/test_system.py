"""API tests for the system endpoints and basic app wiring."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import __version__
from app.core.config import Settings
from app.main import create_app


def test_health_ok_when_database_reachable(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_database_unreachable(test_settings: Settings) -> None:
    # Port 1 on this computer: nothing listens there, so the connection is refused.
    broken = test_settings.model_copy(
        update={"database_url": SecretStr("postgresql://u:p@127.0.0.1:1/none"), "db_connect_timeout": 2}
    )
    with TestClient(create_app(broken)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_version_reports_name_version_and_environment(client: TestClient) -> None:
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    assert response.json() == {
        "name": "FreshLink API",
        "version": __version__,
        "environment": "test",
    }


def test_endpoints_are_only_under_api_v1_prefix(client: TestClient) -> None:
    # /health without the prefix must NOT exist: both apps use /api/v1/...
    assert client.get("/health").status_code == 404


def test_cors_allows_configured_website_origin(client: TestClient) -> None:
    response = client.get("/api/v1/version", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_blocks_unknown_origin(client: TestClient) -> None:
    response = client.get("/api/v1/version", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_docs_available_when_enabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/api/v1/openapi.json").status_code == 200


def test_docs_hidden_when_disabled(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"app_env": "production", "enable_docs": False})
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/api/v1/openapi.json").status_code == 404
