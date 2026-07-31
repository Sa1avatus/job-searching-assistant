"""Store CV documents and link the selected CV to applications."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "sha256"),
    )
    op.create_index("ix_cv_files_user_id", "cv_files", ["user_id"])
    # Adding a column *and* a foreign key on it in one ALTER TABLE is not supported by SQLite;
    # batch mode recreates the table there while emitting plain ALTER TABLE on PostgreSQL, so it
    # is the portable way to write this migration for both engines (see docs/known-limitations.md
    # for which engines are exercised in tests).
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(sa.Column("selected_cv_file_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_applications_selected_cv_file_id",
            "cv_files",
            ["selected_cv_file_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_applications_selected_cv_file_id", ["selected_cv_file_id"])


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_index("ix_applications_selected_cv_file_id")
        batch_op.drop_constraint("fk_applications_selected_cv_file_id", type_="foreignkey")
        batch_op.drop_column("selected_cv_file_id")
    op.drop_index("ix_cv_files_user_id", table_name="cv_files")
    op.drop_table("cv_files")
