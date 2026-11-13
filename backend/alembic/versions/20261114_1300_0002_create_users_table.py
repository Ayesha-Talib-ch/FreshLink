"""Create the users table (one row per account, all four roles)

Generated with `alembic revision --autogenerate`, then reviewed and commented.

- email is CITEXT (case-insensitive) and unique
- phone is stored normalised (+923001234567) and unique
- role / account_status / verification_status are VARCHAR + CHECK constraints
- only an admin can be a super admin (CHECK constraint)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14 13:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=20)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role", _enum("provider", "receiver", "volunteer", "admin", name="user_role"),
            nullable=False,
        ),
        sa.Column(
            "account_status", _enum("active", "deactivated", "suspended", name="account_status"),
            server_default="active", nullable=False,
        ),
        sa.Column(
            "verification_status",
            _enum(
                "not_submitted", "pending", "info_requested", "approved", "rejected",
                name="verification_status",
            ),
            server_default="not_submitted", nullable=False,
        ),
        sa.Column("is_super_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "NOT is_super_admin OR role = 'admin'", name=op.f("ck_users_super_admin_is_admin")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("phone", name=op.f("uq_users_phone")),
    )


def downgrade() -> None:
    op.drop_table("users")
