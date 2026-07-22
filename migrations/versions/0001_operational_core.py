"""Create operational core tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "vacancies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("company", sa.String(300), nullable=False),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("preferred_skills", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "profile_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "category", "name"),
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("vacancy_id", sa.String(36), sa.ForeignKey("vacancies.id"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "vacancy_id"),
    )
    op.create_table(
        "workflow_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id"), nullable=True),
        sa.Column("idempotency_key", sa.String(300), nullable=False, unique=True),
        sa.Column("state", sa.String(50), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "task_transitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_state", sa.String(50), nullable=False),
        sa.Column("new_state", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("worker", sa.String(200), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "task_transitions",
        "workflow_tasks",
        "applications",
        "profile_facts",
        "vacancies",
        "users",
    ):
        op.drop_table(table)
