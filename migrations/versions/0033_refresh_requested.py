"""Add refresh_requested flag to workflow tasks.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_tasks",
        sa.Column("refresh_requested", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("workflow_tasks", "refresh_requested")
