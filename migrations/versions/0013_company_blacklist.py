"""Add per-user company blacklist."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_blacklist",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company", sa.String(300), nullable=False),
        sa.Column("normalized_company", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "normalized_company"),
    )
    op.create_index("ix_company_blacklist_user_id", "company_blacklist", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_company_blacklist_user_id", table_name="company_blacklist")
    op.drop_table("company_blacklist")
