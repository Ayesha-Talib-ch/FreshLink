"""SQLAlchemy models (one module per area, e.g. user.py, listing.py).

Every model module must be imported here so Alembic can "see" all tables.
No tables exist yet: each feature stage adds its own tables + migration.
"""

from app.db.base import Base

__all__ = ["Base"]
