"""Add annotation_feedback table for LTR human annotation.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "annotation_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("cv_files.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("vacancy_id", sa.String(36), sa.ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("feedback_type", sa.String(20), nullable=False, index=True),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("vacancy_a_id", sa.String(36), sa.ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("vacancy_b_id", sa.String(36), sa.ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("a_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("b_reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sampling_reason", sa.String(50), nullable=True),
        sa.Column("current_rank_at_sampling", sa.Integer(), nullable=True),
        sa.Column("ltr_rank_at_sampling", sa.Integer(), nullable=True),
        sa.Column("current_score_at_sampling", sa.Float(), nullable=True),
        sa.Column("ltr_score_at_sampling", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_annotation_feedback_user_resume", "annotation_feedback", ["user_id", "resume_id"])
    op.create_index("ix_annotation_feedback_type", "annotation_feedback", ["feedback_type"])


def downgrade() -> None:
    op.drop_index("ix_annotation_feedback_type", table_name="annotation_feedback")
    op.drop_index("ix_annotation_feedback_user_resume", table_name="annotation_feedback")
    op.drop_table("annotation_feedback")
