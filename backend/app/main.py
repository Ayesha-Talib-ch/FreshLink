"""FreshLink API entry point.

Run from the backend/ folder with:
    uvicorn app.main:app --reload

create_app() is an "application factory": it builds a fresh FastAPI app from
settings. Tests call it with their own settings (e.g. a separate test
database), so they never touch a developer's real data.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.db.session import create_db_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

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

    # Allow the React website (a different origin/port) to call the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # needed later for the httpOnly refresh-token cookie
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
