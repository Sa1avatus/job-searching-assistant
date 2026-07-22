"""Add resumable human-action checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_action_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("resolution_evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_human_action_checkpoints_task_id", "human_action_checkpoints", ["task_id"])
    op.create_index("ix_human_action_checkpoints_status", "human_action_checkpoints", ["status"])


def downgrade() -> None:
    op.drop_index("ix_human_action_checkpoints_status", table_name="human_action_checkpoints")
    op.drop_index("ix_human_action_checkpoints_task_id", table_name="human_action_checkpoints")
    op.drop_table("human_action_checkpoints")
