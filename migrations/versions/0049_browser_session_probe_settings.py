"""Per-site override of how often the background worker checks a saved session's liveness.

Revision ID: 0049
Revises: 0048
Create Date: 2026-09-24

No backfill: a missing row means the built-in default interval applies (see
app.browser.session_probe / app.workers.browser_worker).
"""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_session_probe_settings",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("site_key", sa.String(100), primary_key=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("browser_session_probe_settings")
