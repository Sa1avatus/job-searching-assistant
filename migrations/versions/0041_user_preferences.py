"""User search preferences and constraints.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("min_salary", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("preferred_locations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("work_formats", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("employment_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("min_salary >= 0", name="ck_user_preferences_salary_nonneg"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
