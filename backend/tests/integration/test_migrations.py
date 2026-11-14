"""Migrations must work in both directions and match the models exactly."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

import app.models  # noqa: F401  (registers all models)
from app.db.base import Base
from tests.conftest import alembic_config

REQUIRED_EXTENSIONS = {"pgcrypto", "citext", "pg_trgm"}


def _extensions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())


def test_database_is_at_latest_migration(test_db_engine: Engine) -> None:
    with test_db_engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
        head = ScriptDirectory.from_config(alembic_config(conn)).get_current_head()
    assert current == head


def test_required_extensions_installed(test_db_engine: Engine) -> None:
    assert REQUIRED_EXTENSIONS <= _extensions(test_db_engine)


def test_downgrade_to_base_then_upgrade_again(test_db_engine: Engine) -> None:
    with test_db_engine.begin() as conn:
        command.downgrade(alembic_config(conn), "base")
    assert not (REQUIRED_EXTENSIONS & _extensions(test_db_engine))

    with test_db_engine.begin() as conn:
        command.upgrade(alembic_config(conn), "head")
    assert REQUIRED_EXTENSIONS <= _extensions(test_db_engine)


def test_models_and_migrations_are_in_sync(test_db_engine: Engine) -> None:
    """Fails if someone changes a model but forgets to create a migration."""
    with test_db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"Models differ from the database; run alembic revision --autogenerate: {diff}"


def test_gen_random_uuid_and_citext_work(test_db_engine: Engine) -> None:
    with test_db_engine.connect() as conn:
        assert len(str(conn.execute(text("SELECT gen_random_uuid()")).scalar())) == 36
        assert conn.execute(text("SELECT 'Ali@Mail.com'::citext = 'ali@mail.com'::citext")).scalar()
        assert conn.execute(text("SELECT similarity('biryani', 'biryni') > 0.3")).scalar()


def test_seeded_tables_survive_test_cleanup(test_db_engine: Engine) -> None:
    """Tables with starting data (categories, rules) must not point at tables that the
    test clean-up empties: TRUNCATE ... CASCADE would silently wipe them too."""
    from tests.conftest import SEEDED_TABLES

    with test_db_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT tc.table_name, ccu.table_name AS target FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY'")).all()
    bad = [(table, target) for table, target in rows if table in SEEDED_TABLES and target not in SEEDED_TABLES]
    assert bad == [], f"seeded tables pointing at emptied tables: {bad}"
