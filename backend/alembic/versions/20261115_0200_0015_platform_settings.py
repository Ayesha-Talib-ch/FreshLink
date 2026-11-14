"""Create platform_settings (admin-changed settings)

Generated with `alembic revision --autogenerate`, then reviewed.
Only changed values are stored (key -> JSON value); the defaults live in the code
(app/services/settings_service.py), so nothing needs seeding.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-15 02:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('platform_settings',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_platform_settings_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_platform_settings'))
    )


def downgrade() -> None:
    op.drop_table('platform_settings')
