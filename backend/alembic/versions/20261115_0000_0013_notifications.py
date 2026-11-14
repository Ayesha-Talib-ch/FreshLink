"""Create notification_categories (seeded), notifications and announcements

Generated with `alembic revision --autogenerate`, reviewed, then the categories were
seeded by hand (spec section 13).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15 00:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = [
    # (code, name, description)
    ("request_new", "New requests", "A receiver requested your food"),
    ("request_update", "Request updates", "Requests cancelled or expired"),
    ("request_decision", "Request decisions", "Your request was approved, partially approved or rejected"),
    ("allocation_update", "Allocation changes", "An allocation was cancelled"),
    ("collection_update", "Collection updates", "Food handed over, received, or a collection failed"),
    ("listing_urgency", "Listing urgency", "Your listing is close to its deadline or expired"),
    ("listing_match", "Relevant food nearby", "New food that matches your needs"),
    ("assignment_update", "Delivery tasks", "Delivery tasks assigned to you or changed"),
    ("verification_result", "Verification result", "Your account was approved, rejected or needs information"),
    ("admin_alert", "Administrator alerts", "New registrations, documents to review, failed collections"),
    ("announcement", "Announcements", "Messages from FreshLink administrators"),
]


def upgrade() -> None:
    op.create_table('notification_categories',
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('default_push', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_notification_categories'))
    )
    op.create_table('announcements',
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('audience_type', sa.String(length=10), nullable=False),
    sa.Column('audience_role', sa.String(length=20), nullable=True),
    sa.Column('recipient_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_announcements_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_announcements'))
    )
    op.create_table('notifications',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('category_code', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=True),
    sa.Column('entity_id', sa.Uuid(), nullable=True),
    sa.Column('is_read', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('push_status', sa.String(length=20), server_default='not_sent', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.ForeignKeyConstraint(['category_code'], ['notification_categories.code'], name=op.f('fk_notifications_category_code_notification_categories'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index('ix_notifications_user_unread_created', 'notifications', ['user_id', 'is_read', sa.literal_column('created_at DESC')], unique=False)

    table = sa.table("notification_categories", sa.column("code"), sa.column("name"), sa.column("description"))
    op.bulk_insert(table, [{"code": c, "name": n, "description": d} for c, n, d in CATEGORIES])


def downgrade() -> None:
    op.drop_index('ix_notifications_user_unread_created', table_name='notifications')
    op.drop_table('notifications')
    op.drop_table('announcements')
    op.drop_table('notification_categories')
