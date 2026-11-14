"""Machine learning: provider_daily_logs, ml_models and surplus_predictions

Generated with `alembic revision --autogenerate`, then reviewed.
- provider_daily_logs: one row per provider, day and food category; the database itself checks
  that surplus = max(0, produced - sold), so it can never be typed in wrongly
- ml_models: the model registry; a partial unique index allows one active model per name
- surplus_predictions: every estimate given, so accuracy can be measured later

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-15 04:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('ml_models',
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('version', sa.String(length=60), nullable=False),
    sa.Column('algorithm', sa.String(length=60), nullable=False),
    sa.Column('artifact', sa.String(length=200), nullable=False),
    sa.Column('data_source', sa.String(length=20), nullable=False),
    sa.Column('training_rows', sa.Integer(), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('trained_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ml_models')),
    sa.UniqueConstraint('name', 'version', name='name_version')
    )
    op.create_index('uq_ml_models_one_active', 'ml_models', ['name'], unique=True, postgresql_where=sa.text('is_active'))
    op.create_table('provider_daily_logs',
    sa.Column('provider_id', sa.Uuid(), nullable=False),
    sa.Column('log_date', sa.Date(), nullable=False),
    sa.Column('food_category_id', sa.Uuid(), nullable=False),
    sa.Column('unit', sa.Enum('kg', 'litre', 'piece', 'package', 'portion', 'tray', name='log_quantity_unit', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('produced_qty', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('sold_qty', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('surplus_qty', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('source', sa.Enum('manual', 'import', 'synthetic', name='log_source', native_enum=False, create_constraint=True, length=20), server_default='manual', nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('produced_qty >= 0 AND sold_qty >= 0 AND surplus_qty >= 0', name=op.f('ck_provider_daily_logs_quantities_not_negative')),
    sa.CheckConstraint('surplus_qty = GREATEST(produced_qty - sold_qty, 0)', name=op.f('ck_provider_daily_logs_surplus_is_produced_minus_sold')),
    sa.ForeignKeyConstraint(['food_category_id'], ['food_categories.id'], name=op.f('fk_provider_daily_logs_food_category_id_food_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['provider_id'], ['provider_profiles.id'], name=op.f('fk_provider_daily_logs_provider_id_provider_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provider_daily_logs')),
    sa.UniqueConstraint('provider_id', 'log_date', 'food_category_id', name='one_log_per_day_and_category')
    )
    op.create_index(op.f('ix_provider_daily_logs_provider_id'), 'provider_daily_logs', ['provider_id'], unique=False)
    op.create_table('surplus_predictions',
    sa.Column('provider_id', sa.Uuid(), nullable=False),
    sa.Column('food_category_id', sa.Uuid(), nullable=False),
    sa.Column('target_date', sa.Date(), nullable=False),
    sa.Column('unit', sa.Enum('kg', 'litre', 'piece', 'package', 'portion', 'tray', name='prediction_quantity_unit', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('predicted_qty', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('lower_qty', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('upper_qty', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('method', sa.Enum('model', 'own_average', 'category_median', name='estimate_method', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('confidence', sa.String(length=10), nullable=False),
    sa.Column('model_version', sa.String(length=60), nullable=True),
    sa.Column('planned_production', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['food_category_id'], ['food_categories.id'], name=op.f('fk_surplus_predictions_food_category_id_food_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['provider_id'], ['provider_profiles.id'], name=op.f('fk_surplus_predictions_provider_id_provider_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_surplus_predictions'))
    )
    op.create_index('ix_surplus_predictions_provider_created', 'surplus_predictions', ['provider_id', sa.literal_column('created_at DESC')], unique=False)


def downgrade() -> None:
    op.drop_index('ix_surplus_predictions_provider_created', table_name='surplus_predictions')
    op.drop_table('surplus_predictions')
    op.drop_index(op.f('ix_provider_daily_logs_provider_id'), table_name='provider_daily_logs')
    op.drop_table('provider_daily_logs')
    op.drop_index('uq_ml_models_one_active', table_name='ml_models', postgresql_where=sa.text('is_active'))
    op.drop_table('ml_models')
