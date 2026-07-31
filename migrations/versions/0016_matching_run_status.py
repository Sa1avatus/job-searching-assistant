"""Add durable matching run status and failure diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_match_results",
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
    )
    op.add_column(
        "application_match_results",
        sa.Column("run_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("fallback_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_match_results",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "application_match_results",
        "eligibility_status",
        existing_type=sa.String(50),
        nullable=False,
        server_default="pending",
    )
    op.alter_column(
        "application_match_results",
        "final_score",
        existing_type=sa.Float(),
        nullable=False,
        server_default="0",
    )
    op.alter_column(
        "application_match_results",
        "scoring_version",
        existing_type=sa.String(100),
        nullable=False,
        server_default="pending",
    )
    op.execute(
        "UPDATE application_match_results "
        "SET status = 'scored', updated_at = calculated_at "
        "WHERE calculated_at IS NOT NULL"
    )
    op.create_index(
        "ix_application_match_results_status",
        "application_match_results",
        ["status"],
    )
    op.create_index(
        "ix_application_match_results_run_id",
        "application_match_results",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_match_results_run_id",
        table_name="application_match_results",
    )
    op.drop_index(
        "ix_application_match_results_status",
        table_name="application_match_results",
    )
    op.alter_column(
        "application_match_results",
        "scoring_version",
        existing_type=sa.String(100),
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "application_match_results",
        "final_score",
        existing_type=sa.Float(),
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "application_match_results",
        "eligibility_status",
        existing_type=sa.String(50),
        nullable=False,
        server_default=None,
    )
    op.drop_column("application_match_results", "updated_at")
    op.drop_column("application_match_results", "started_at")
    op.drop_column("application_match_results", "failure_reason")
    op.drop_column("application_match_results", "fallback_reason")
    op.drop_column("application_match_results", "run_id")
    op.drop_column("application_match_results", "status")
