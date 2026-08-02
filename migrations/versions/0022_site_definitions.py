"""Create arbitrary site definition persistence."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_definitions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("login_url", sa.Text(), nullable=False),
        sa.Column(
            "allowed_hosts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "authorization_rules",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "site_key",
            name="uq_site_definitions_user_site_key",
        ),
    )
    op.create_index(
        "ix_site_definitions_user_id",
        "site_definitions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_site_definitions_user_id", table_name="site_definitions")
    op.drop_table("site_definitions")
