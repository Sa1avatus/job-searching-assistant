"""Search recipes for user-defined job sites.

Revision ID: 0048
Revises: 0047
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_search_recipes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "site_definition_id",
            sa.String(36),
            sa.ForeignKey("site_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("recipe", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("learned_from_url", sa.Text(), nullable=True),
        sa.Column("preview", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("site_definition_id", "version", name="uq_site_search_recipes_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')", name="ck_site_search_recipes_status"
        ),
    )
    op.create_index(
        "uq_site_search_recipes_one_active",
        "site_search_recipes",
        ["site_definition_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_site_search_recipes_one_active", table_name="site_search_recipes")
    op.drop_table("site_search_recipes")
