"""Named, freezable train/validation/test splits of the human label dataset.

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annotation_splits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("seed", sa.Integer(), nullable=False, server_default="42"),
        sa.Column("ratios", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("eval_vacancies", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("dataset_hash", sa.String(64), nullable=True),
        sa.Column("label_counts", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("annotation_splits")
