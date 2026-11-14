"""Application settings.

All configuration comes from environment variables or .env files. Nothing
secret is ever written in source code. pydantic-settings reads the values,
converts them to the right Python types and fails early with a clear error
message if a value is invalid.

Two .env files are read (later ones win):
  1. FreshLink/.env          - database user/password, shared with docker-compose.yml
  2. FreshLink/backend/.env  - backend-only settings (CORS, docs, later JWT secret)
So the database password is written in only ONE place.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

from app import __version__

# backend/ folder (this file is backend/app/core/config.py)
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FreshLink API"
    app_version: str = __version__
    app_env: Literal["development", "test", "production"] = "development"

    # Every endpoint lives under this prefix, e.g. /api/v1/health
    api_v1_prefix: str = "/api/v1"

    # Website origins allowed to call the API from a browser (CORS).
    # The mobile app is not a browser, so CORS does not apply to it.
    # NoDecode: read the raw comma-separated text; the validator below splits it.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    enable_docs: bool = True

    # ---------- Database ----------
    # Either give a full DATABASE_URL (typical on a hosting service) ...
    database_url: SecretStr | None = None
    # ... or the separate parts (typical in development with docker-compose).
    postgres_user: str = "freshlink"
    postgres_password: SecretStr | None = None
    # 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first,
    # which Docker doesn't listen on, so every new connection waited ~5 s.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "freshlink"
    # Seconds to wait for PostgreSQL before giving up (keeps /health fast).
    db_connect_timeout: int = 5

    # ---------- File storage ----------
    # Private folder for uploaded documents (git-ignored). Never served as static files.
    storage_dir: Path = BACKEND_DIR / "storage"
    max_upload_mb: int = Field(default=5, ge=1, le=50)
    max_files_per_user: int = Field(default=30, ge=1)

    # ---------- Machine learning (Stage 10) ----------
    # Trained model files (git-ignored; regenerated with `python -m app.cli train-surplus`).
    ml_artifacts_dir: Path = BACKEND_DIR.parent / "ml" / "artifacts"

    # ---------- Authentication ----------
    # Secret key that signs login tokens. Anyone who knows it can forge a token
    # for any user, so it lives only in backend/.env and must be long and random.
    jwt_secret: SecretStr | None = None
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=24 * 60)
    refresh_token_expire_days: int = Field(default=14, ge=1, le=90)

    # Website refresh-token cookie. "Secure" means the browser only sends it
    # over HTTPS (browsers also allow it on http://localhost for development).
    refresh_cookie_name: str = "freshlink_refresh"
    refresh_cookie_secure: bool = True

    # Brute-force protection: after this many FAILED logins within the window,
    # further attempts for that account, or from that IP address, are refused.
    # The IP limit is higher on purpose: in Pakistan, mobile networks put many
    # users behind ONE shared IP address (carrier-grade NAT), so a low IP limit
    # would lock out innocent people who share it.
    login_max_failures_per_account: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_failure_window_seconds: int = Field(default=60, ge=1)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_comma_separated(cls, value: object) -> object:
        """Allow CORS_ORIGINS=http://a,http://b in the .env file."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def get_jwt_secret(self) -> str:
        if self.jwt_secret is None:
            raise ValueError(
                "JWT_SECRET is not set. Add a long random value to backend/.env "
                "(see backend/.env.example for how to generate one)."
            )
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET is too short: use at least 32 random characters.")
        return secret

    def get_database_url(self) -> URL:
        """Build the SQLAlchemy connection URL (driver: psycopg 3).

        SecretStr hides the password if settings are ever printed or logged;
        .get_secret_value() is the only way to read it.
        """
        if self.database_url is not None:
            url = make_url(self.database_url.get_secret_value())
            # Hosting services give "postgres://" or "postgresql://"; force psycopg 3.
            return url.set(drivername="postgresql+psycopg")

        if self.postgres_password is None:
            raise ValueError(
                "No database password configured. Copy FreshLink/.env.example to "
                "FreshLink/.env and set POSTGRES_PASSWORD (or set DATABASE_URL)."
            )
        # URL.create escapes special characters in the password safely.
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings object (read once, then cached)."""
    return Settings()
