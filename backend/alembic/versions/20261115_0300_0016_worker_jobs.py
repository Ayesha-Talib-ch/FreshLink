"""Background worker: job_runs table and collections.reminder_sent_at

Generated with `alembic revision --autogenerate`, then reviewed.
- job_runs: one row per run of a background job (status, counts, error)
- collections.reminder_sent_at: the "pickup soon" reminder is sent only once

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-15 03:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('job_runs',
    sa.Column('job', sa.String(length=40), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_runs'))
    )
    op.create_index('ix_job_runs_job_started', 'job_runs', ['job', sa.literal_column('started_at DESC')], unique=False)
    op.add_column('collections', sa.Column('reminder_sent_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('collections', 'reminder_sent_at')
    op.drop_index('ix_job_runs_job_started', table_name='job_runs')
    op.drop_table('job_runs')
