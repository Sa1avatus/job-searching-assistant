"""Add claim pipeline columns to application match results.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns for claim-based scoring to application_match_results
    with op.batch_alter_table("application_match_results") as batch_op:
        batch_op.add_column(
            sa.Column("required_score", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("preferred_score", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("bonus_score", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=False, server_default="0"))

    # Add entailment columns to requirement_matches
    with op.batch_alter_table("requirement_matches") as batch_op:
        batch_op.add_column(sa.Column("entailment_relation", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("evidence_strength", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("is_hard_blocker", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("requirement_matches") as batch_op:
        batch_op.drop_column("is_hard_blocker")
        batch_op.drop_column("evidence_strength")
        batch_op.drop_column("entailment_relation")

    with op.batch_alter_table("application_match_results") as batch_op:
        batch_op.drop_column("confidence")
        batch_op.drop_column("bonus_score")
        batch_op.drop_column("preferred_score")
        batch_op.drop_column("required_score")
