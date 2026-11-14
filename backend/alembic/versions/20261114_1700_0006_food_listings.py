"""Create food_listings and listing_status_history

Generated with `alembic revision --autogenerate`, then reviewed.

Safety rules enforced by the database itself:
- total_quantity > 0, and 0 <= allocated_quantity <= total_quantity (no over-allocation)
- pickup window end after start; collection deadline not before the window starts
- declaration_accepted must be true
- GIN trigram index on food_name for fuzzy search (needs the pg_trgm extension, migration 0001)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14 17:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('food_listings',
    sa.Column('provider_id', sa.Uuid(), nullable=False),
    sa.Column('food_category_id', sa.Uuid(), nullable=False),
    sa.Column('food_name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('unit', sa.Enum('kg', 'litre', 'piece', 'package', 'portion', 'tray', name='listing_quantity_unit', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('total_quantity', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('allocated_quantity', sa.Numeric(precision=10, scale=2), server_default=sa.text('0'), nullable=False),
    sa.Column('preparation_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('packaging_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('best_before_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expiry_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pickup_window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('pickup_window_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('collection_deadline', sa.DateTime(timezone=True), nullable=False),
    sa.Column('pickup_address', sa.String(length=300), nullable=False),
    sa.Column('pickup_latitude', sa.Double(), nullable=False),
    sa.Column('pickup_longitude', sa.Double(), nullable=False),
    sa.Column('storage_info', sa.Text(), nullable=True),
    sa.Column('distribution_type', sa.Enum('free_donation', name='distribution_type', native_enum=False, create_constraint=True, length=20), server_default='free_donation', nullable=False),
    sa.Column('image_file_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('active', 'paused', 'fully_allocated', 'closed', 'expired', 'suspended', 'removed', name='listing_status', native_enum=False, create_constraint=True, length=20), server_default='active', nullable=False),
    sa.Column('declaration_accepted', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('allocated_quantity >= 0 AND allocated_quantity <= total_quantity', name=op.f('ck_food_listings_allocated_within_total')),
    sa.CheckConstraint('collection_deadline >= pickup_window_start', name=op.f('ck_food_listings_deadline_after_window_start')),
    sa.CheckConstraint('declaration_accepted', name=op.f('ck_food_listings_declaration_required')),
    sa.CheckConstraint('pickup_latitude BETWEEN -90 AND 90', name=op.f('ck_food_listings_latitude_range')),
    sa.CheckConstraint('pickup_longitude BETWEEN -180 AND 180', name=op.f('ck_food_listings_longitude_range')),
    sa.CheckConstraint('pickup_window_end > pickup_window_start', name=op.f('ck_food_listings_pickup_window_order')),
    sa.CheckConstraint('total_quantity > 0', name=op.f('ck_food_listings_total_quantity_positive')),
    sa.ForeignKeyConstraint(['food_category_id'], ['food_categories.id'], name=op.f('fk_food_listings_food_category_id_food_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['image_file_id'], ['files.id'], name=op.f('fk_food_listings_image_file_id_files'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['provider_id'], ['provider_profiles.id'], name=op.f('fk_food_listings_provider_id_provider_profiles'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_food_listings'))
    )
    op.create_index(op.f('ix_food_listings_food_category_id'), 'food_listings', ['food_category_id'], unique=False)
    op.create_index('ix_food_listings_food_name_trgm', 'food_listings', ['food_name'], unique=False, postgresql_using='gin', postgresql_ops={'food_name': 'gin_trgm_ops'})
    op.create_index(op.f('ix_food_listings_provider_id'), 'food_listings', ['provider_id'], unique=False)
    op.create_index('ix_food_listings_status_deadline', 'food_listings', ['status', 'collection_deadline'], unique=False)
    op.create_table('listing_status_history',
    sa.Column('listing_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=False),
    sa.Column('changed_by', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['changed_by'], ['users.id'], name=op.f('fk_listing_status_history_changed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['listing_id'], ['food_listings.id'], name=op.f('fk_listing_status_history_listing_id_food_listings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_listing_status_history'))
    )
    op.create_index(op.f('ix_listing_status_history_listing_id'), 'listing_status_history', ['listing_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_listing_status_history_listing_id'), table_name='listing_status_history')
    op.drop_table('listing_status_history')
    op.drop_index('ix_food_listings_status_deadline', table_name='food_listings')
    op.drop_index(op.f('ix_food_listings_provider_id'), table_name='food_listings')
    op.drop_index('ix_food_listings_food_name_trgm', table_name='food_listings', postgresql_using='gin', postgresql_ops={'food_name': 'gin_trgm_ops'})
    op.drop_index(op.f('ix_food_listings_food_category_id'), table_name='food_listings')
    op.drop_table('food_listings')
