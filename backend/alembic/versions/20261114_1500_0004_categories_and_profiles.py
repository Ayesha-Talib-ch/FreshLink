"""Create category and profile tables, and insert the starting categories

Generated with `alembic revision --autogenerate`, reviewed, then the seed data
was added by hand (spec sections 2.2 and 2.3).

Tables: provider_categories, receiver_categories, food_categories,
provider_profiles, receiver_profiles, receiver_food_preferences,
volunteer_profiles, volunteer_availability.

The receiver priority weights are PLACEHOLDERS for administrators to adjust
in the admin stage; they are not a policy decision.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14 15:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER_CATEGORIES = [
    # (code, name, description)
    ("bakery", "Bakery", "Bread, buns, cakes, pastries and other bakery products"),
    ("restaurant", "Restaurant", "Rice, biryani, prepared meals and other cooked food"),
    ("fast_food", "Fast-food shop", "Burgers, pizza, sandwiches and other fast food"),
    ("grocery", "Grocery store / Supermarket", "Fruits, vegetables, packaged food and other groceries"),
]

RECEIVER_CATEGORIES = [
    # (code, name, description, priority_weight [placeholder], requires_documents)
    ("ngo", "Welfare organization / NGO", "Organizations that collect food for people who need assistance", 1.0, True),
    ("community_kitchen", "Community kitchen", "Groups that prepare or distribute meals within communities", 1.0, True),
    ("community_group", "Community group", "Registered groups that organize food distribution for communities", 0.8, True),
    ("individual", "Individual / household", "Individuals or families who need access to surplus food", 0.7, True),
    ("small_business", "Small food business", "Eligible small food businesses; donated food must not be resold", 0.5, True),
]

FOOD_CATEGORIES = [
    # (code, name, perishability_class, requires_preparation_time, requires_expiry_date)
    ("bakery_items", "Bakery items", "bakery", False, False),
    ("cooked_meals", "Cooked meals", "cooked", True, False),
    ("fast_food", "Fast food", "cooked", True, False),
    ("fresh_produce", "Fruits and vegetables", "fresh_produce", False, False),
    ("dairy", "Dairy products", "dairy", False, True),
    ("packaged_food", "Packaged food", "packaged", False, True),
    ("other", "Other food", "other", False, False),
]


def _seed() -> None:
    simple = lambda name: sa.table(  # noqa: E731
        name, sa.column("code"), sa.column("name"), sa.column("description"), sa.column("sort_order")
    )
    op.bulk_insert(
        simple("provider_categories"),
        [{"code": c, "name": n, "description": d, "sort_order": i} for i, (c, n, d) in enumerate(PROVIDER_CATEGORIES)],
    )
    receivers = sa.table(
        "receiver_categories", sa.column("code"), sa.column("name"), sa.column("description"),
        sa.column("sort_order"), sa.column("priority_weight"), sa.column("requires_documents"),
    )
    op.bulk_insert(receivers, [
        {"code": c, "name": n, "description": d, "sort_order": i, "priority_weight": w, "requires_documents": r}
        for i, (c, n, d, w, r) in enumerate(RECEIVER_CATEGORIES)
    ])
    foods = sa.table(
        "food_categories", sa.column("code"), sa.column("name"), sa.column("sort_order"),
        sa.column("perishability_class"), sa.column("requires_preparation_time"), sa.column("requires_expiry_date"),
    )
    op.bulk_insert(foods, [
        {"code": c, "name": n, "sort_order": i, "perishability_class": p,
         "requires_preparation_time": prep, "requires_expiry_date": exp}
        for i, (c, n, p, prep, exp) in enumerate(FOOD_CATEGORIES)
    ])


def upgrade() -> None:
    op.create_table('food_categories',
    sa.Column('perishability_class', sa.Enum('cooked', 'bakery', 'fresh_produce', 'dairy', 'packaged', 'other', name='perishability_class', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('requires_preparation_time', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('requires_expiry_date', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('meals_per_unit', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_food_categories')),
    sa.UniqueConstraint('code', name=op.f('uq_food_categories_code')),
    sa.UniqueConstraint('name', name=op.f('uq_food_categories_name'))
    )
    op.create_table('provider_categories',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provider_categories')),
    sa.UniqueConstraint('code', name=op.f('uq_provider_categories_code')),
    sa.UniqueConstraint('name', name=op.f('uq_provider_categories_name'))
    )
    op.create_table('receiver_categories',
    sa.Column('priority_weight', sa.Numeric(precision=4, scale=2), server_default=sa.text('1.0'), nullable=False),
    sa.Column('requires_documents', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('priority_weight > 0 AND priority_weight <= 10', name=op.f('ck_receiver_categories_priority_weight_range')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_receiver_categories')),
    sa.UniqueConstraint('code', name=op.f('uq_receiver_categories_code')),
    sa.UniqueConstraint('name', name=op.f('uq_receiver_categories_name'))
    )
    op.create_table('provider_profiles',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('business_name', sa.String(length=150), nullable=False),
    sa.Column('provider_category_id', sa.Uuid(), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=False),
    sa.Column('latitude', sa.Double(), nullable=False),
    sa.Column('longitude', sa.Double(), nullable=False),
    sa.Column('contact_person', sa.String(length=120), nullable=False),
    sa.Column('contact_phone', sa.String(length=16), nullable=False),
    sa.Column('opening_hours', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_provider_profiles_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_provider_profiles_longitude_range')),
    sa.ForeignKeyConstraint(['provider_category_id'], ['provider_categories.id'], name=op.f('fk_provider_profiles_provider_category_id_provider_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_provider_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provider_profiles')),
    sa.UniqueConstraint('user_id', name=op.f('uq_provider_profiles_user_id'))
    )
    op.create_index(op.f('ix_provider_profiles_provider_category_id'), 'provider_profiles', ['provider_category_id'], unique=False)
    op.create_table('receiver_profiles',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('receiver_name', sa.String(length=150), nullable=False),
    sa.Column('receiver_category_id', sa.Uuid(), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=False),
    sa.Column('latitude', sa.Double(), nullable=False),
    sa.Column('longitude', sa.Double(), nullable=False),
    sa.Column('max_pickup_radius_km', sa.Numeric(precision=5, scale=1), server_default=sa.text('10'), nullable=False),
    sa.Column('has_own_transport', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('household_size', sa.SmallInteger(), nullable=True),
    sa.Column('is_eligible', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('eligibility_notes', sa.Text(), nullable=True),
    sa.Column('not_for_resale_declared', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('household_size IS NULL OR household_size BETWEEN 1 AND 50', name=op.f('ck_receiver_profiles_household_size_range')),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_receiver_profiles_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_receiver_profiles_longitude_range')),
    sa.CheckConstraint('max_pickup_radius_km > 0 AND max_pickup_radius_km <= 100', name=op.f('ck_receiver_profiles_radius_range')),
    sa.ForeignKeyConstraint(['receiver_category_id'], ['receiver_categories.id'], name=op.f('fk_receiver_profiles_receiver_category_id_receiver_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_receiver_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_receiver_profiles')),
    sa.UniqueConstraint('user_id', name=op.f('uq_receiver_profiles_user_id'))
    )
    op.create_index(op.f('ix_receiver_profiles_receiver_category_id'), 'receiver_profiles', ['receiver_category_id'], unique=False)
    op.create_table('volunteer_profiles',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=False),
    sa.Column('latitude', sa.Double(), nullable=False),
    sa.Column('longitude', sa.Double(), nullable=False),
    sa.Column('has_vehicle', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('vehicle_type', sa.Enum('bicycle', 'motorbike', 'rickshaw', 'car', 'van', 'other', name='vehicle_type', native_enum=False, create_constraint=True, length=20), nullable=True),
    sa.Column('service_radius_km', sa.Numeric(precision=5, scale=1), server_default=sa.text('5'), nullable=False),
    sa.Column('is_available_now', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('has_vehicle OR vehicle_type IS NULL', name=op.f('ck_volunteer_profiles_vehicle_type_needs_vehicle')),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_volunteer_profiles_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_volunteer_profiles_longitude_range')),
    sa.CheckConstraint('service_radius_km > 0 AND service_radius_km <= 100', name=op.f('ck_volunteer_profiles_radius_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_volunteer_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_volunteer_profiles')),
    sa.UniqueConstraint('user_id', name=op.f('uq_volunteer_profiles_user_id'))
    )
    op.create_table('receiver_food_preferences',
    sa.Column('receiver_id', sa.Uuid(), nullable=False),
    sa.Column('food_category_id', sa.Uuid(), nullable=False),
    sa.Column('max_quantity_per_pickup', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('unit', sa.Enum('kg', 'litre', 'piece', 'package', 'portion', 'tray', name='quantity_unit', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('max_quantity_per_pickup > 0', name=op.f('ck_receiver_food_preferences_max_quantity_positive')),
    sa.ForeignKeyConstraint(['food_category_id'], ['food_categories.id'], name=op.f('fk_receiver_food_preferences_food_category_id_food_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['receiver_id'], ['receiver_profiles.id'], name=op.f('fk_receiver_food_preferences_receiver_id_receiver_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('receiver_id', 'food_category_id', name=op.f('pk_receiver_food_preferences'))
    )
    op.create_table('volunteer_availability',
    sa.Column('volunteer_id', sa.Uuid(), nullable=False),
    sa.Column('day_of_week', sa.SmallInteger(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('day_of_week BETWEEN 0 AND 6', name=op.f('ck_volunteer_availability_day_of_week_range')),
    sa.CheckConstraint('end_time > start_time', name=op.f('ck_volunteer_availability_end_after_start')),
    sa.ForeignKeyConstraint(['volunteer_id'], ['volunteer_profiles.id'], name=op.f('fk_volunteer_availability_volunteer_id_volunteer_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_volunteer_availability'))
    )
    op.create_index(op.f('ix_volunteer_availability_volunteer_id'), 'volunteer_availability', ['volunteer_id'], unique=False)

    _seed()


def downgrade() -> None:
    op.drop_index(op.f('ix_volunteer_availability_volunteer_id'), table_name='volunteer_availability')
    op.drop_table('volunteer_availability')
    op.drop_table('receiver_food_preferences')
    op.drop_table('volunteer_profiles')
    op.drop_index(op.f('ix_receiver_profiles_receiver_category_id'), table_name='receiver_profiles')
    op.drop_table('receiver_profiles')
    op.drop_index(op.f('ix_provider_profiles_provider_category_id'), table_name='provider_profiles')
    op.drop_table('provider_profiles')
    op.drop_table('receiver_categories')
    op.drop_table('provider_categories')
    op.drop_table('food_categories')
