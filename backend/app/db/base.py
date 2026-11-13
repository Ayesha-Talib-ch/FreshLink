"""Base class for every SQLAlchemy model (database table).

Each table class (User, FoodListing, ...) will inherit from Base. Alembic
compares Base.metadata with the real database to generate migrations.

The naming convention gives every index and constraint a predictable name
(e.g. "ck_food_listings_total_quantity_positive"). Without it PostgreSQL
invents names, and later migrations can't reliably find them to change them.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
