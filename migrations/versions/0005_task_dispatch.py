"""Add task scheduling and priority fields for worker dispatch."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_tasks",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workflow_tasks",
        sa.Column(
            "scheduled_for",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_workflow_tasks_priority", "workflow_tasks", ["priority"])
    op.create_index("ix_workflow_tasks_scheduled_for", "workflow_tasks", ["scheduled_for"])


def downgrade() -> None:
    op.drop_index("ix_workflow_tasks_scheduled_for", table_name="workflow_tasks")
    op.drop_index("ix_workflow_tasks_priority", table_name="workflow_tasks")
    op.drop_column("workflow_tasks", "scheduled_for")
    op.drop_column("workflow_tasks", "priority")
