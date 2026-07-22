"""Align ORM-declared foreign-key indexes with the migrated schema."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_vacancy_id", "applications", ["vacancy_id"])
    op.create_index("ix_profile_facts_user_id", "profile_facts", ["user_id"])
    op.create_index("ix_task_transitions_task_id", "task_transitions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_transitions_task_id", table_name="task_transitions")
    op.drop_index("ix_profile_facts_user_id", table_name="profile_facts")
    op.drop_index("ix_applications_vacancy_id", table_name="applications")
    op.drop_index("ix_applications_user_id", table_name="applications")
