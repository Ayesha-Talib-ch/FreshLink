"""FreshLink API entry point.

Run from the backend/ folder with:
    uvicorn app.main:app --reload

create_app() is an "application factory": it builds a fresh FastAPI app from
settings. Tests call it with their own settings (e.g. a separate test
database), so they never touch a developer's real data.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.errors import CatchUnexpectedErrorsMiddleware, ErrorResponse, register_error_handlers
from app.core.rate_limit import LoginThrottle
from app.core.storage import LocalStorage
from app.db.session import create_db_engine, create_session_factory


def _operation_id(route: APIRoute) -> str:
    """Readable operation names like "auth-login" instead of "login_api_v1_auth_login_post".
    The generated TypeScript client for the website and app uses these names."""
    return f"{route.tags[0]}-{route.name}" if route.tags else route.name


def _install_openapi(app: FastAPI) -> None:
    """Make the Swagger docs describe the errors the API REALLY returns.

    FastAPI documents every 422 with its own {"detail": [...]} shape, but our
    handler returns {"error": {...}} (core/errors.py). This replaces that, and
    documents 401/403 on every endpoint that needs a login.
    """

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        error_schema = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(error_schema.pop("$defs", {}))
        components["ErrorResponse"] = error_schema
        error_ref = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}

        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                responses = operation.setdefault("responses", {})
                if "422" in responses:
                    responses["422"] = {"description": "Validation error: details.fields lists each problem",
                                        "content": error_ref}
                if operation.get("security"):
                    responses.setdefault("401", {"description": "Not logged in, or the token is invalid/expired",
                                                 "content": error_ref})
                    responses.setdefault("403", {"description": "Not allowed (wrong role, not verified, suspended)",
                                                 "content": error_ref})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    # Fail at start-up (not at the first login) if the token secret is missing or weak.
    settings.get_jwt_secret()

    # Creating the engine does NOT connect yet; the first query does.
    # So the API can start even if PostgreSQL is down (and /health reports it).
    engine = create_db_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield  # the app runs here
        engine.dispose()  # close all pooled DB connections on shutdown

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        generate_unique_id_function=_operation_id,
        # Swagger UI (/docs), ReDoc (/redoc) and the OpenAPI schema can be
        # switched off in production with ENABLE_DOCS=false.
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json" if settings.enable_docs else None,
    )

    # Shared objects for dependencies (see app/api/deps.py).
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.storage = LocalStorage(settings.storage_dir)
    app.state.login_throttle = LoginThrottle(
        per_account=settings.login_max_failures_per_account,
        per_ip=settings.login_max_failures_per_ip,
        window_seconds=settings.login_failure_window_seconds,
    )

    # ORDER MATTERS: the middleware added LAST runs FIRST (outermost).
    # 1) crash catcher (inner)  2) CORS (outer), so even 500 errors get CORS headers.
    app.add_middleware(CatchUnexpectedErrorsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # needed for the httpOnly refresh-token cookie
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    _install_openapi(app)
    return app


app = create_app()
