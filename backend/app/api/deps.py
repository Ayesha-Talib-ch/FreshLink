"""Reusable FastAPI dependencies.

A "dependency" is a function FastAPI runs before an endpoint and whose result
it passes into the endpoint (Depends(...)). Later stages add
get_current_user, require_roles(...) and require_verified() here.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Settings of the running app (set in create_app)."""
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Give the endpoint a database session and always close it afterwards.

    If the endpoint raises an error, everything it did in the database is
    rolled back, so a failed request never leaves half-saved data.
    Services call session.commit() themselves when their work succeeds.
    """
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
