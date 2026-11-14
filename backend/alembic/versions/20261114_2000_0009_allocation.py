"""Create allocation_runs, allocation_run_lines and allocations

Generated with `alembic revision --autogenerate`, then reviewed.

- allocation_runs keep a JSONB snapshot of every input (reproducible suggestions)
- run lines: 0 <= suggested <= requested
- allocations: approved > 0, one allocation per request (unique request_id)
- RESTRICT foreign keys: allocations are history and are never deleted with a listing

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14 20:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('allocation_runs',
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('policy', sa.Enum('priority_weighted', 'proportional_fair', 'first_come', 'manual', name='allocation_policy', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('solver', sa.Text(), server_default='greedy', nullable=False),
    sa.Column('status', sa.Enum('draft', 'confirmed', 'discarded', 'superseded', name='allocation_run_status', native_enum=False, create_constraint=True, length=20), server_default='draft', nullable=False),
    sa.Column('available_snapshot', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('listing_version', sa.Integer(), nullable=False),
    sa.Column('input_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('confirmed_by', sa.Uuid(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_allocation_runs_confirmed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_allocation_runs_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['listing_id'], ['food_listings.id'], name=op.f('fk_allocation_runs_listing_id_food_listings'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_allocation_runs'))
    )
    op.create_index(op.f('ix_allocation_runs_listing_id'), 'allocation_runs', ['listing_id'], unique=False)
    op.create_table('allocation_run_lines',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('requested_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('cap_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('priority_weight', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('suggested_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.CheckConstraint('suggested_quantity >= 0 AND suggested_quantity <= requested_quantity', name=op.f('ck_allocation_run_lines_suggested_within_requested')),
    sa.ForeignKeyConstraint(['request_id'], ['food_requests.id'], name=op.f('fk_allocation_run_lines_request_id_food_requests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['run_id'], ['allocation_runs.id'], name=op.f('fk_allocation_run_lines_run_id_allocation_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_allocation_run_lines'))
    )
    op.create_index(op.f('ix_allocation_run_lines_run_id'), 'allocation_run_lines', ['run_id'], unique=False)
    op.create_table('allocations',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('receiver_id', sa.Uuid(), nullable=False),
    sa.Column('suggested_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('approved_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('status', sa.Enum('confirmed', 'in_collection', 'collected', 'completed', 'failed', 'cancelled', name='allocation_status', native_enum=False, create_constraint=True, length=20), server_default='confirmed', nullable=False),
    sa.Column('decision_reason', sa.Text(), nullable=False),
    sa.Column('override_reason', sa.Text(), nullable=True),
    sa.Column('confirmed_by', sa.Uuid(), nullable=True),
    sa.Column('cancelled_reason', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('approved_quantity > 0', name=op.f('ck_allocations_approved_quantity_positive')),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_allocations_confirmed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['listing_id'], ['food_listings.id'], name=op.f('fk_allocations_listing_id_food_listings'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['receiver_id'], ['receiver_profiles.id'], name=op.f('fk_allocations_receiver_id_receiver_profiles'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['request_id'], ['food_requests.id'], name=op.f('fk_allocations_request_id_food_requests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['run_id'], ['allocation_runs.id'], name=op.f('fk_allocations_run_id_allocation_runs'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_allocations')),
    sa.UniqueConstraint('request_id', name=op.f('uq_allocations_request_id'))
    )
    op.create_index(op.f('ix_allocations_listing_id'), 'allocations', ['listing_id'], unique=False)
    op.create_index(op.f('ix_allocations_receiver_id'), 'allocations', ['receiver_id'], unique=False)
    op.create_index(op.f('ix_allocations_run_id'), 'allocations', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_allocations_run_id'), table_name='allocations')
    op.drop_index(op.f('ix_allocations_receiver_id'), table_name='allocations')
    op.drop_index(op.f('ix_allocations_listing_id'), table_name='allocations')
    op.drop_table('allocations')
    op.drop_index(op.f('ix_allocation_run_lines_run_id'), table_name='allocation_run_lines')
    op.drop_table('allocation_run_lines')
    op.drop_index(op.f('ix_allocation_runs_listing_id'), table_name='allocation_runs')
    op.drop_table('allocation_runs')
