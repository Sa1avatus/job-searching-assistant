"""Add pipeline progress tracking columns to application_match_results.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_match_results",
        sa.Column("requirements_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "application_match_results",
        sa.Column("requirements_processed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "application_match_results",
        sa.Column("llm_calls_made", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("application_match_results", "llm_calls_made")
    op.drop_column("application_match_results", "requirements_processed")
    op.drop_column("application_match_results", "requirements_total")
