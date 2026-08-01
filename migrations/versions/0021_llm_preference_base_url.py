"""Add an optional endpoint URL to per-user LLM preferences."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_preferences", sa.Column("base_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_preferences", "base_url")
