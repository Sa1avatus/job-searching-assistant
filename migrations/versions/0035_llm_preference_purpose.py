"""Split LLM preferences by purpose (matching vs materials).

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # llm_preferences gains a `purpose` discriminator and a composite primary key
    # (user_id, purpose). Existing rows are backfilled to `materials` (the historical
    # single-model preference); matching falls back to it until a matching-specific
    # model is saved. PostgreSQL only: the Alembic chain is not required to run
    # against SQLite (see docs/database.md).
    op.add_column(
        "llm_preferences",
        sa.Column("purpose", sa.String(50), nullable=False, server_default="materials"),
    )
    op.drop_constraint("llm_preferences_pkey", "llm_preferences", type_="primary")
    op.create_primary_key("pk_llm_preferences", "llm_preferences", ["user_id", "purpose"])


def downgrade() -> None:
    op.drop_constraint("pk_llm_preferences", "llm_preferences", type_="primary")
    op.create_primary_key("llm_preferences_pkey", "llm_preferences", ["user_id"])
    op.drop_column("llm_preferences", "purpose")
