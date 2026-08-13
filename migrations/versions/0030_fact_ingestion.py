"""Extend profile_facts for provenance and add fact_import_batches.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend profile_facts with provenance columns
    with op.batch_alter_table("profile_facts") as batch_op:
        batch_op.add_column(
            sa.Column("source_type", sa.String(50), nullable=False, server_default="manual")
        )
        batch_op.add_column(sa.Column("source_id", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("source_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("extraction_method", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("batch_id", sa.String(36), nullable=True))
        batch_op.add_column(
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0")
        )
        batch_op.add_column(sa.Column("experience_started_at", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("experience_ended_at", sa.String(20), nullable=True))
        batch_op.add_column(
            sa.Column("status", sa.String(30), nullable=False, server_default="active")
        )

    # Create fact_import_batches table
    op.create_table(
        "fact_import_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=True),
        sa.Column("source_filename", sa.String(255), nullable=True),
        sa.Column("extractor_version", sa.String(50), nullable=False, server_default="1"),
        sa.Column("facts_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("facts_merged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("facts_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("facts_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(30), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fact_import_batches")

    with op.batch_alter_table("profile_facts") as batch_op:
        batch_op.drop_column("status")
        batch_op.drop_column("experience_ended_at")
        batch_op.drop_column("experience_started_at")
        batch_op.drop_column("confidence")
        batch_op.drop_column("batch_id")
        batch_op.drop_column("extraction_method")
        batch_op.drop_column("source_text")
        batch_op.drop_column("source_id")
        batch_op.drop_column("source_type")
