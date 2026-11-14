"""Create match_recommendations (stored matching snapshots with outcomes)

Generated with `alembic revision --autogenerate`, then reviewed.
One row per (listing, receiver): score, rank, score parts, reasons, model version,
and the outcome (requested / completed): training data for the ML ranker (plan 7.1).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14 21:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('match_recommendations',
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('receiver_id', sa.Uuid(), nullable=False),
    sa.Column('score', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('components', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('model_version', sa.String(length=40), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('outcome', sa.Enum('none', 'requested', 'completed', name='match_outcome', native_enum=False, create_constraint=True, length=20), server_default='none', nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['food_listings.id'], name=op.f('fk_match_recommendations_listing_id_food_listings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['receiver_id'], ['receiver_profiles.id'], name=op.f('fk_match_recommendations_receiver_id_receiver_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_recommendations')),
    sa.UniqueConstraint('listing_id', 'receiver_id', name='uq_match_recommendations_listing_receiver')
    )
    op.create_index(op.f('ix_match_recommendations_listing_id'), 'match_recommendations', ['listing_id'], unique=False)
    op.create_index(op.f('ix_match_recommendations_receiver_id'), 'match_recommendations', ['receiver_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_match_recommendations_receiver_id'), table_name='match_recommendations')
    op.drop_index(op.f('ix_match_recommendations_listing_id'), table_name='match_recommendations')
    op.drop_table('match_recommendations')
