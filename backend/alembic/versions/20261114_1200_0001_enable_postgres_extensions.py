"""Enable PostgreSQL extensions used by FreshLink (Technical Plan section 3.1)

- pgcrypto: cryptographic helpers (gen_random_uuid() for UUID primary keys
            is built into PostgreSQL 13+, pgcrypto is kept for compatibility)
- citext:   case-insensitive text, so "Ali@Mail.com" and "ali@mail.com" are
            the same email address (used by the users table)
- pg_trgm:  trigram fuzzy search, so "biryni" still finds "Biryani"

No tables are created here: each feature stage adds its own tables in its own
migration (users and auth come next).

Revision ID: 0001
Revises:
Create Date: 2026-11-14 12:00:00 UTC
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pgcrypto", "citext", "pg_trgm")


def upgrade() -> None:
    for name in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{name}"')


def downgrade() -> None:
    for name in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{name}"')
