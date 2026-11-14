"""System endpoints: is the API running, can it reach the database, which version is it?

The web and mobile apps (and the team) use these to check connectivity.
"""

import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import AppSettings, DbSession

router = APIRouter(tags=["system"])
logger = logging.getLogger("freshlink")


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    database: str  # "ok" | "unavailable"


class VersionResponse(BaseModel):
    name: str
    version: str
    environment: str


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Database unreachable"}},
)
def health(db: DbSession, response: Response) -> HealthResponse:
    """Returns 200 when the API and PostgreSQL both work, 503 when the database is down."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Health check: database unreachable: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="unavailable")
    return HealthResponse(status="ok", database="ok")


@router.get("/version", response_model=VersionResponse)
def version(settings: AppSettings) -> VersionResponse:
    return VersionResponse(
        name=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
