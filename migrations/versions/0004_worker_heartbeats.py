"""Persist worker heartbeats for operational monitoring."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
