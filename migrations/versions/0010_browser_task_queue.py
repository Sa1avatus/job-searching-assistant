"""Route typed tasks to isolated durable worker queues."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_tasks",
        sa.Column("queue_name", sa.String(50), nullable=False, server_default="dispatcher"),
    )
    op.add_column(
        "workflow_tasks",
        sa.Column("task_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("ix_workflow_tasks_queue_name", "workflow_tasks", ["queue_name"])
    op.create_index(
        "ix_workflow_tasks_queue_claim",
        "workflow_tasks",
        ["queue_name", "state", "scheduled_for"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_tasks_queue_claim", table_name="workflow_tasks")
    op.drop_index("ix_workflow_tasks_queue_name", table_name="workflow_tasks")
    op.drop_column("workflow_tasks", "task_payload")
    op.drop_column("workflow_tasks", "queue_name")
