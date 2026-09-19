"""Strategy recommendations and their decisions (closed-loop job strategy).

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("dimension", sa.String(30), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed", index=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("applied", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("baseline", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "user_id", "fingerprint", name="uq_strategy_recommendations_fingerprint"
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected')",
            name="ck_strategy_recommendations_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("strategy_recommendations")
