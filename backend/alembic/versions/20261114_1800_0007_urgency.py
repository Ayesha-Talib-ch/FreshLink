"""Urgency rules per food category, and urgency columns on food_listings

Generated with `alembic revision --autogenerate`, reviewed, then extended by hand:
- seed one PLACEHOLDER rule per food category (admins adjust them; not safety limits)
- an explicit CHECK constraint for food_listings.urgency_level (Alembic's add_column
  does not create the CHECK that a new table would get)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14 18:00:00 UTC
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# food category code: (medium_hours, high_hours, critical_hours, max_hours_since_preparation)
# Demo placeholders for platform PRIORITISATION, not food-safety limits (plan section 1.3).
PLACEHOLDER_RULES = {
    "bakery_items": (48, 24, 6, 72),
    "cooked_meals": (12, 6, 2, 12),
    "fast_food": (12, 6, 2, 8),
    "fresh_produce": (72, 24, 8, None),
    "dairy": (72, 24, 6, None),
    "packaged_food": (168, 48, 12, None),
    "other": (48, 24, 6, None),
}
LEVELS = ("normal", "medium", "high", "critical", "incomplete")


def upgrade() -> None:
    op.create_table(
        "urgency_rules",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("food_category_id", sa.Uuid(), nullable=False),
        sa.Column("medium_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("high_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("critical_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("max_hours_since_preparation", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("critical_hours > 0 AND critical_hours < high_hours AND high_hours < medium_hours",
                           name=op.f("ck_urgency_rules_threshold_order")),
        sa.CheckConstraint("max_hours_since_preparation IS NULL OR max_hours_since_preparation > 0",
                           name=op.f("ck_urgency_rules_max_since_preparation_positive")),
        sa.ForeignKeyConstraint(["food_category_id"], ["food_categories.id"],
                                name=op.f("fk_urgency_rules_food_category_id_food_categories"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_urgency_rules")),
        sa.UniqueConstraint("food_category_id", name=op.f("uq_urgency_rules_food_category_id")),
    )
    conn = op.get_bind()
    for code, (medium, high, critical, max_prep) in PLACEHOLDER_RULES.items():
        conn.execute(
            sa.text("INSERT INTO urgency_rules (food_category_id, medium_hours, high_hours, critical_hours, "
                    "max_hours_since_preparation) SELECT id, :m, :h, :c, :p FROM food_categories WHERE code = :code"),
            {"m": medium, "h": high, "c": critical, "p": max_prep, "code": code},
        )

    op.add_column("food_listings", sa.Column("urgency_level", sa.String(length=20),
                                             server_default="normal", nullable=False))
    op.add_column("food_listings", sa.Column("urgency_computed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(op.f("ck_food_listings_urgency_level"), "food_listings",
                               "urgency_level IN (" + ", ".join(f"'{v}'" for v in LEVELS) + ")")
    op.create_index("ix_food_listings_status_urgency", "food_listings", ["status", "urgency_level"])


def downgrade() -> None:
    op.drop_index("ix_food_listings_status_urgency", table_name="food_listings")
    op.drop_constraint(op.f("ck_food_listings_urgency_level"), "food_listings", type_="check")
    op.drop_column("food_listings", "urgency_computed_at")
    op.drop_column("food_listings", "urgency_level")
    op.drop_table("urgency_rules")
