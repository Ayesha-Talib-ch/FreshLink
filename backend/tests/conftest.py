"""Shared pytest fixtures.

Database tests run against a REAL PostgreSQL (the docker-compose "db"
container), as the Technical Plan requires (section 10.1). They use a separate
database called "freshlink_test", so tests never touch your development data.
The test database is created automatically and migrated to the latest version
once per test run.

Tests that don't need a database (e.g. tests/unit) don't use these fixtures.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "freshlink_test"


def _base_settings() -> Settings:
    """Real connection details from the .env files (password, host, port)."""
    return Settings()


def _test_db_url() -> URL:
    try:
        url = _base_settings().get_database_url()
    except ValueError as exc:
        pytest.fail(f"{exc}\nDatabase tests need the docker-compose database.", pytrace=False)
    return url.set(database=TEST_DB_NAME)


def alembic_config(connection: object) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["connection"] = connection
    cfg.attributes["configure_logger"] = False  # keep pytest output clean
    return cfg


def _create_test_database_if_missing(url: URL) -> None:
    # CREATE DATABASE can't run inside a transaction, hence AUTOCOMMIT.
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    except OperationalError as exc:
        pytest.fail(
            "Cannot connect to PostgreSQL. Is Docker Desktop running and the database "
            "started?\n  cd D:\\University\\FreshLink\n  docker compose up -d\n\n"
            f"Original error: {exc.orig}",
            pytrace=False,
        )
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def test_db_engine() -> Iterator[Engine]:
    url = _test_db_url()
    _create_test_database_if_missing(url)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def test_settings(test_db_engine: Engine) -> Settings:
    url = test_db_engine.url.render_as_string(hide_password=False)
    return _base_settings().model_copy(
        update={
            "app_env": "test",
            "database_url": SecretStr(url),
            "cors_origins": ["http://localhost:5173"],
            "enable_docs": True,
        }
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    # "with" runs the app's startup/shutdown (lifespan), like the real server.
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client
