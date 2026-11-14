"""Unit tests for settings parsing (no database needed)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make sure a developer's shell variables can't change these tests.
    for name in ("DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_HOST", "APP_ENV", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173 ,")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_invalid_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging-typo")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_enable_docs_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_DOCS", "false")
    assert Settings(_env_file=None).enable_docs is False


def test_defaults_are_safe_without_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.app_env == "development"
    assert settings.postgres_host == "127.0.0.1"


def test_database_url_built_from_parts_and_password_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/w:rd")
    url = Settings(_env_file=None).get_database_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss/w:rd"  # special characters survive
    assert (url.host, url.port, url.database) == ("127.0.0.1", 5432, "freshlink")
    assert "p@ss/w:rd" not in str(url)  # hidden when printed


def test_full_database_url_wins_and_uses_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@db.example.com:6543/prod")
    url = Settings(_env_file=None).get_database_url()
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database) == ("db.example.com", 6543, "prod")


def test_missing_password_gives_clear_error() -> None:
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        Settings(_env_file=None).get_database_url()


def test_password_is_hidden_when_settings_are_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "super-secret-value")
    assert "super-secret-value" not in repr(Settings(_env_file=None))
