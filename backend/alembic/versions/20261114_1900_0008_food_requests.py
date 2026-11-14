"""Create food_requests and request_status_history

Generated with `alembic revision --autogenerate`, then reviewed.

- requested_quantity > 0
- partial unique index: at most ONE active (pending / approved / partially approved)
  request per receiver per listing; cancelled or rejected ones don't block a new request
- listing and receiver FKs are RESTRICT: requests are history and are never deleted
  together with a listing or profile

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14 19:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('food_requests',
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('receiver_id', sa.Uuid(), nullable=False),
    sa.Column('requested_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=True),
    sa.Column('collection_preference', sa.Enum('self_pickup', 'needs_volunteer', name='collection_preference', native_enum=False, create_constraint=True, length=20), server_default='self_pickup', nullable=False),
    sa.Column('preferred_pickup_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Enum('pending', 'approved', 'partially_approved', 'rejected', 'cancelled', 'expired', 'completed', name='request_status', native_enum=False, create_constraint=True, length=20), server_default='pending', nullable=False),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('requested_quantity > 0', name=op.f('ck_food_requests_requested_quantity_positive')),
    sa.ForeignKeyConstraint(['listing_id'], ['food_listings.id'], name=op.f('fk_food_requests_listing_id_food_listings'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['receiver_id'], ['receiver_profiles.id'], name=op.f('fk_food_requests_receiver_id_receiver_profiles'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_food_requests'))
    )
    op.create_index(op.f('ix_food_requests_listing_id'), 'food_requests', ['listing_id'], unique=False)
    op.create_index(op.f('ix_food_requests_receiver_id'), 'food_requests', ['receiver_id'], unique=False)
    op.create_index(op.f('ix_food_requests_status'), 'food_requests', ['status'], unique=False)
    op.create_index('uq_food_requests_one_active_per_receiver', 'food_requests', ['listing_id', 'receiver_id'], unique=True, postgresql_where=sa.text("status IN ('pending', 'approved', 'partially_approved')"))
    op.create_table('request_status_history',
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=False),
    sa.Column('changed_by', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], name=op.f('fk_request_status_history_changed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['request_id'], ['food_requests.id'], name=op.f('fk_request_status_history_request_id_food_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_request_status_history'))
    )
    op.create_index(op.f('ix_request_status_history_request_id'), 'request_status_history', ['request_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_request_status_history_request_id'), table_name='request_status_history')
    op.drop_table('request_status_history')
    op.drop_index('uq_food_requests_one_active_per_receiver', table_name='food_requests', postgresql_where=sa.text("status IN ('pending', 'approved', 'partially_approved')"))
    op.drop_index(op.f('ix_food_requests_status'), table_name='food_requests')
    op.drop_index(op.f('ix_food_requests_receiver_id'), table_name='food_requests')
    op.drop_index(op.f('ix_food_requests_listing_id'), table_name='food_requests')
    op.drop_table('food_requests')
