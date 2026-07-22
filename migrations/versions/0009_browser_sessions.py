"""Store metadata for encrypted persistent browser sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_key", sa.String(100), nullable=False),
        sa.Column("adapter_name", sa.String(100), nullable=False),
        sa.Column("encrypted_state_path", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("last_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "site_key"),
    )
    op.create_index("ix_browser_sessions_user_id", "browser_sessions", ["user_id"])
    op.create_index("ix_browser_sessions_site_key", "browser_sessions", ["site_key"])
    op.create_index("ix_browser_sessions_status", "browser_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_browser_sessions_status", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_site_key", table_name="browser_sessions")
    op.drop_index("ix_browser_sessions_user_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
