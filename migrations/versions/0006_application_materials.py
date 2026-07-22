"""Store editable application materials and grounded screening answers."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("cover_letter_text", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "application_answers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_id", sa.String(300), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("semantic_category", sa.String(100), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answer_source", sa.String(50), nullable=False),
        sa.Column("source_fact_name", sa.String(200), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("application_id", "field_id"),
    )
    op.create_index(
        "ix_application_answers_application_id", "application_answers", ["application_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_application_answers_application_id", table_name="application_answers")
    op.drop_table("application_answers")
    op.drop_column("applications", "cover_letter_text")
