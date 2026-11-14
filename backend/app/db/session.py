"""Database engine and session factory.

- The ENGINE manages a pool of open connections to PostgreSQL (reusing
  connections is much faster than opening a new one for every request).
- A SESSION is one "unit of work": the queries of one API request, committed
  or rolled back together as a transaction.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.get_database_url(),
        pool_pre_ping=True,  # silently replace connections that the DB has closed
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after commit, so an
    # endpoint can return the row it just saved without another query.
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
