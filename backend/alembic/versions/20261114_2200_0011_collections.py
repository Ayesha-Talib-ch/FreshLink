"""Create collections (handover and receipt of allocated food)

Generated with `alembic revision --autogenerate`, reviewed, then extended by hand with a
data step: every allocation that is still confirmed gets its collection record, using the
receiver's requested collection preference.

- one collection per allocation (unique allocation_id)
- handed_over_quantity > 0; 0 < received_quantity <= handed_over_quantity

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14 22:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = """
INSERT INTO collections (allocation_id, method, scheduled_pickup_at)
SELECT a.id,
       CASE WHEN r.collection_preference = 'needs_volunteer' THEN 'volunteer' ELSE 'self_pickup' END,
       r.preferred_pickup_at
FROM allocations a JOIN food_requests r ON r.id = a.request_id
WHERE a.status = 'confirmed'
  AND NOT EXISTS (SELECT 1 FROM collections c WHERE c.allocation_id = a.id)
"""


def upgrade() -> None:
    op.create_table('collections',
    sa.Column('allocation_id', sa.Uuid(), nullable=False),
    sa.Column('method', sa.Enum('self_pickup', 'volunteer', name='collection_method', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('scheduled_pickup_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Enum('scheduled', 'handed_over', 'received', 'failed', 'cancelled', name='collection_status', native_enum=False, create_constraint=True, length=20), server_default='scheduled', nullable=False),
    sa.Column('handed_over_quantity', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('handed_over_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('handed_over_by', sa.Uuid(), nullable=True),
    sa.Column('received_quantity', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received_by', sa.Uuid(), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('handed_over_quantity IS NULL OR handed_over_quantity > 0', name=op.f('ck_collections_handed_over_positive')),
    sa.CheckConstraint('received_quantity IS NULL OR (handed_over_quantity IS NOT NULL AND received_quantity > 0 AND received_quantity <= handed_over_quantity)', name=op.f('ck_collections_received_within_handed_over')),
    sa.ForeignKeyConstraint(['allocation_id'], ['allocations.id'], name=op.f('fk_collections_allocation_id_allocations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['handed_over_by'], ['users.id'], name=op.f('fk_collections_handed_over_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['received_by'], ['users.id'], name=op.f('fk_collections_received_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_collections')),
    sa.UniqueConstraint('allocation_id', name=op.f('uq_collections_allocation_id'))
    )
    op.create_index(op.f('ix_collections_status'), 'collections', ['status'], unique=False)

    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_index(op.f('ix_collections_status'), table_name='collections')
    op.drop_table('collections')
