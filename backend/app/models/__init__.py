"""SQLAlchemy models (one module per area, e.g. user.py, listing.py).

Every model module must be imported here so Alembic can "see" all tables.
"""

from app.db.base import Base
from app.models.user import User

__all__ = ["Base", "User"]
