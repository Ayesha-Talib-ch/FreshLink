"""Shared pytest fixtures.

Database tests run against a REAL PostgreSQL (the docker-compose "db"
container), as the Technical Plan requires (section 10.1). They use a separate
database called "freshlink_test", so tests never touch your development data.
The test database is created automatically and migrated to the latest version
once per test run, and every table is emptied after each test.

Tests that don't need a database (e.g. tests/unit) don't use these fixtures.
"""

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.domain.enums import AccountStatus, UserRole, VerificationStatus
from app.main import create_app
from app.models.user import User

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB_NAME = "freshlink_test"
TEST_JWT_SECRET = "test-only-secret-" + "x" * 40
DEFAULT_PASSWORD = "Correct-Horse-42"


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


# Tables filled by migrations with starting data (categories). They are NOT emptied
# between tests; tests must not change them (or must undo their changes).
SEEDED_TABLES = {"provider_categories", "receiver_categories", "food_categories", "verification_requirements",
                 "urgency_rules", "notification_categories"}


@pytest.fixture(autouse=True)
def _empty_tables_after_test(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every test starts with empty tables, so tests can't affect each other."""
    yield
    if "test_db_engine" not in request.fixturenames:
        return
    engine: Engine = request.getfixturevalue("test_db_engine")
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name not in SEEDED_TABLES)
    if tables:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def test_settings(test_db_engine: Engine, tmp_path: Path) -> Settings:
    url = test_db_engine.url.render_as_string(hide_password=False)
    return _base_settings().model_copy(
        update={
            "app_env": "test",
            "storage_dir": tmp_path / "storage",  # uploads go to a temporary folder per test
            "database_url": SecretStr(url),
            "jwt_secret": SecretStr(TEST_JWT_SECRET),
            "access_token_expire_minutes": 15,
            "cors_origins": ["http://localhost:5173"],
            "enable_docs": True,
        }
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    # "with" runs the app's startup/shutdown (lifespan), like the real server.
    with TestClient(create_app(test_settings)) as test_client:
        yield test_client


@pytest.fixture
def db_session(test_db_engine: Engine) -> Iterator[Session]:
    """A session for preparing or inspecting data directly in the test database."""
    with Session(test_db_engine, expire_on_commit=False) as session:
        yield session


_phone_counter = itertools.count(1)

MakeUser = Callable[..., User]


@pytest.fixture
def make_user(db_session: Session) -> MakeUser:
    """Create a user straight in the database. Example: make_user(role=UserRole.ADMIN)."""

    def _make(
        *,
        role: UserRole = UserRole.PROVIDER,
        email: str | None = None,
        phone: str | None = None,
        password: str = DEFAULT_PASSWORD,
        verification_status: VerificationStatus = VerificationStatus.NOT_SUBMITTED,
        account_status: AccountStatus = AccountStatus.ACTIVE,
        is_super_admin: bool = False,
        full_name: str = "Test User",
    ) -> User:
        n = next(_phone_counter)
        user = User(
            email=email or f"user{n}@example.com",
            phone=phone or f"+92300{n:07d}",
            full_name=full_name,
            password_hash=hash_password(password),
            role=role,
            verification_status=verification_status,
            account_status=account_status,
            is_super_admin=is_super_admin,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return _make


def login_headers(client: TestClient, identifier: str, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
