"""Browser session lifecycle state per user and site.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-18

No backfill: a missing row is bootstrapped from the existing browser_sessions row at read
time (see app.services.browser_session_state), so legacy sessions keep working.
"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_session_states",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("site_key", sa.String(100), primary_key=True),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_browser_session_states_state", "browser_session_states", ["state"])


def downgrade() -> None:
    op.drop_index("ix_browser_session_states_state", table_name="browser_session_states")
    op.drop_table("browser_session_states")
