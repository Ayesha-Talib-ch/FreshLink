"""Create volunteer_assignments and assignment_status_history

Generated with `alembic revision --autogenerate`, reviewed, then extended by hand with a
data step: every scheduled collection whose method is "volunteer" gets an OPEN task.

- partial unique index: at most one ACTIVE assignment per collection
- pickup / drop-off points are snapshots taken when the task is created

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-14 23:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = """
INSERT INTO volunteer_assignments (collection_id, pickup_address, pickup_latitude, pickup_longitude,
                                   dropoff_address, dropoff_latitude, dropoff_longitude, scheduled_pickup_at)
SELECT c.id, l.pickup_address, l.pickup_latitude, l.pickup_longitude, r.address, r.latitude, r.longitude,
       c.scheduled_pickup_at
FROM collections c
JOIN allocations a ON a.id = c.allocation_id
JOIN food_listings l ON l.id = a.listing_id
JOIN receiver_profiles r ON r.id = a.receiver_id
WHERE c.method = 'volunteer' AND c.status = 'scheduled'
"""


def upgrade() -> None:
    op.create_table('volunteer_assignments',
    sa.Column('collection_id', sa.Uuid(), nullable=False),
    sa.Column('volunteer_id', sa.Uuid(), nullable=True),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('open', 'assigned', 'accepted', 'declined', 'en_route_pickup', 'collected', 'en_route_dropoff', 'delivered', 'completed', 'failed', 'cancelled', name='assignment_status', native_enum=False, create_constraint=True, length=20), server_default='open', nullable=False),
    sa.Column('pickup_address', sa.String(length=300), nullable=False),
    sa.Column('pickup_latitude', sa.Double(), nullable=False),
    sa.Column('pickup_longitude', sa.Double(), nullable=False),
    sa.Column('dropoff_address', sa.String(length=300), nullable=False),
    sa.Column('dropoff_latitude', sa.Double(), nullable=False),
    sa.Column('dropoff_longitude', sa.Double(), nullable=False),
    sa.Column('scheduled_pickup_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decline_reason', sa.Text(), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_volunteer_assignments_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], name=op.f('fk_volunteer_assignments_collection_id_collections'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['volunteer_id'], ['volunteer_profiles.id'], name=op.f('fk_volunteer_assignments_volunteer_id_volunteer_profiles'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_volunteer_assignments'))
    )
    op.create_index(op.f('ix_volunteer_assignments_collection_id'), 'volunteer_assignments', ['collection_id'], unique=False)
    op.create_index(op.f('ix_volunteer_assignments_status'), 'volunteer_assignments', ['status'], unique=False)
    op.create_index(op.f('ix_volunteer_assignments_volunteer_id'), 'volunteer_assignments', ['volunteer_id'], unique=False)
    op.create_index('uq_volunteer_assignments_one_active_per_collection', 'volunteer_assignments', ['collection_id'], unique=True, postgresql_where=sa.text("status IN ('open', 'assigned', 'accepted', 'en_route_pickup', 'collected', 'en_route_dropoff', 'delivered')"))
    op.create_table('assignment_status_history',
    sa.Column('assignment_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=False),
    sa.Column('changed_by', sa.Uuid(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['assignment_id'], ['volunteer_assignments.id'], name=op.f('fk_assignment_status_history_assignment_id_volunteer_assignments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], name=op.f('fk_assignment_status_history_changed_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assignment_status_history'))
    )
    op.create_index(op.f('ix_assignment_status_history_assignment_id'), 'assignment_status_history', ['assignment_id'], unique=False)

    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_index(op.f('ix_assignment_status_history_assignment_id'), table_name='assignment_status_history')
    op.drop_table('assignment_status_history')
    op.drop_index('uq_volunteer_assignments_one_active_per_collection', table_name='volunteer_assignments', postgresql_where=sa.text("status IN ('open', 'assigned', 'accepted', 'en_route_pickup', 'collected', 'en_route_dropoff', 'delivered')"))
    op.drop_index(op.f('ix_volunteer_assignments_volunteer_id'), table_name='volunteer_assignments')
    op.drop_index(op.f('ix_volunteer_assignments_status'), table_name='volunteer_assignments')
    op.drop_index(op.f('ix_volunteer_assignments_collection_id'), table_name='volunteer_assignments')
    op.drop_table('volunteer_assignments')
