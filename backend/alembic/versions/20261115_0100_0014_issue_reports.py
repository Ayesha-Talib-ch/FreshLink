"""Create issue_reports ("report a problem")

Generated with `alembic revision --autogenerate`, then reviewed.

- entity_type + entity_id point to a listing, request, collection or delivery task
  (no foreign key: it can point to four tables; the API checks the item exists)
- partial unique index: one unresolved report per reporter per item (no double reports)
- CHECK: a resolved report must have a resolution note (shown to the reporter)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15 01:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('issue_reports',
    sa.Column('reporter_id', sa.Uuid(), nullable=False),
    sa.Column('entity_type', sa.Enum('listing', 'request', 'collection', 'assignment', name='issue_entity_type', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.Enum('wrong_information', 'quantity_mismatch', 'food_condition', 'no_show', 'delay', 'behaviour', 'inappropriate', 'other', name='issue_category', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('open', 'in_review', 'resolved', name='issue_status', native_enum=False, create_constraint=True, length=20), server_default='open', nullable=False),
    sa.Column('resolved_by', sa.Uuid(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], name=op.f('fk_issue_reports_reporter_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_issue_reports_resolved_by_users'), ondelete='SET NULL'),
    sa.CheckConstraint("status <> 'resolved' OR resolution_note IS NOT NULL", name=op.f('ck_issue_reports_resolved_needs_note')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_issue_reports'))
    )
    op.create_index(op.f('ix_issue_reports_reporter_id'), 'issue_reports', ['reporter_id'], unique=False)
    op.create_index('ix_issue_reports_status_created', 'issue_reports', ['status', 'created_at'], unique=False)
    op.create_index('uq_issue_reports_one_open_per_reporter', 'issue_reports', ['reporter_id', 'entity_type', 'entity_id'], unique=True, postgresql_where=sa.text("status <> 'resolved'"))


def downgrade() -> None:
    op.drop_index('uq_issue_reports_one_open_per_reporter', table_name='issue_reports', postgresql_where=sa.text("status <> 'resolved'"))
    op.drop_index('ix_issue_reports_status_created', table_name='issue_reports')
    op.drop_index(op.f('ix_issue_reports_reporter_id'), table_name='issue_reports')
    op.drop_table('issue_reports')
