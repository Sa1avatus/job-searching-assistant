"""Add resolved + previous_status to email events for the review resolution flow.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_email_events",
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "application_email_events",
        sa.Column("previous_status", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_email_events", "previous_status")
    op.drop_column("application_email_events", "resolved")
