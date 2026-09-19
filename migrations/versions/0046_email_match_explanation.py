"""Explainable email linking: match method/reason and the reason a human is needed.

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_email_events", sa.Column("match_method", sa.String(20)))
    op.add_column("application_email_events", sa.Column("match_reason", sa.Text()))
    op.add_column("application_email_events", sa.Column("review_reason", sa.Text()))


def downgrade() -> None:
    op.drop_column("application_email_events", "review_reason")
    op.drop_column("application_email_events", "match_reason")
    op.drop_column("application_email_events", "match_method")
