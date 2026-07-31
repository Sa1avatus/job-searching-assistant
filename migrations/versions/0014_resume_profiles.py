"""Store extracted profile data per resume and track the active resume."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cv_files",
        sa.Column("skills", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "cv_files",
        sa.Column("experience_summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "cv_files",
        sa.Column("search_keywords", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("cv_files", sa.Column("years_of_experience", sa.Float(), nullable=True))
    op.add_column("cv_files", sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("active_cv_file_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_users_active_cv_file_id",
        "users",
        "cv_files",
        ["active_cv_file_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_active_cv_file_id", "users", ["active_cv_file_id"])

    # Preserve the former single-resume behavior: choose the newest uploaded resume and attach
    # the existing verified resume-derived facts to that file. Other uploaded files remain
    # explicitly unanalyzed rather than receiving facts that may not belong to them.
    op.execute(
        """
        UPDATE users
        SET active_cv_file_id = (
            SELECT cv_files.id
            FROM cv_files
            WHERE cv_files.user_id = users.id
            ORDER BY cv_files.created_at DESC, cv_files.id DESC
            LIMIT 1
        )
        WHERE active_cv_file_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE cv_files
        SET skills = COALESCE((
                SELECT json_agg(profile_facts.name ORDER BY profile_facts.created_at)
                FROM profile_facts
                WHERE profile_facts.user_id = cv_files.user_id
                  AND profile_facts.category = 'skill'
                  AND profile_facts.is_verified = true
            ), '[]'::json),
            experience_summary = COALESCE((
                SELECT profile_facts.value
                FROM profile_facts
                WHERE profile_facts.user_id = cv_files.user_id
                  AND profile_facts.category = 'experience_summary'
                  AND profile_facts.is_verified = true
                ORDER BY profile_facts.created_at DESC
                LIMIT 1
            ), ''),
            analyzed_at = CASE
                WHEN EXISTS (
                    SELECT 1 FROM profile_facts
                    WHERE profile_facts.user_id = cv_files.user_id
                      AND profile_facts.is_verified = true
                      AND profile_facts.category IN ('skill', 'experience_summary')
                ) THEN CURRENT_TIMESTAMP
                ELSE NULL
            END
        FROM users
        WHERE users.active_cv_file_id = cv_files.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_users_active_cv_file_id", table_name="users")
    op.drop_constraint("fk_users_active_cv_file_id", "users", type_="foreignkey")
    op.drop_column("users", "active_cv_file_id")
    op.drop_column("cv_files", "analyzed_at")
    op.drop_column("cv_files", "years_of_experience")
    op.drop_column("cv_files", "search_keywords")
    op.drop_column("cv_files", "experience_summary")
    op.drop_column("cv_files", "skills")
