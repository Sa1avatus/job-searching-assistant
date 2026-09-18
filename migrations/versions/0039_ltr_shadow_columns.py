"""LTR shadow mode columns.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-23 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add LTR shadow mode columns to application_match_results
    op.add_column(
        "application_match_results",
        sa.Column("ltr_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("ltr_status", sa.String(50), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("ltr_rank", sa.Integer(), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("rank_delta", sa.Integer(), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("ltr_topk_overlap", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # Remove LTR shadow mode columns from application_match_results
    op.drop_column("application_match_results", "ltr_topk_overlap")
    op.drop_column("application_match_results", "rank_delta")
    op.drop_column("application_match_results", "ltr_rank")
    op.drop_column("application_match_results", "ltr_status")
    op.drop_column("application_match_results", "ltr_score")
