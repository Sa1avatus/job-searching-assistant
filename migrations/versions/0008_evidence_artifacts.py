"""Register root-confined evidence artifacts for secure review access."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "checkpoint_id",
            sa.String(36),
            sa.ForeignKey("human_action_checkpoints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False, unique=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_artifacts_checkpoint_id", "evidence_artifacts", ["checkpoint_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_artifacts_checkpoint_id", table_name="evidence_artifacts")
    op.drop_table("evidence_artifacts")
