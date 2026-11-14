"""Alembic runtime: connects to the database and runs migrations.

The database URL comes from app settings (the .env files), never from
alembic.ini, so no password is ever committed. Tests can pass their own
connection through config.attributes["connection"] (see tests/conftest.py).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import app.models  # noqa: F401  (imports every model so Base.metadata knows all tables)
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,  # also detect column type changes
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Write SQL to the screen instead of running it (alembic upgrade head --sql)."""
    url = get_settings().get_database_url().render_as_string(hide_password=False)
    _configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # provided by tests
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    settings = get_settings()
    engine = create_engine(
        settings.get_database_url(),
        connect_args={"connect_timeout": settings.db_connect_timeout},  # never hang forever
    )
    try:
        with engine.connect() as conn:
            _configure(connection=conn)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
