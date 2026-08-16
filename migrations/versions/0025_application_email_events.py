import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_email_events",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.String(36),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("message_fingerprint", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column(
            "status_applied",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "message_fingerprint",
            name="uq_application_email_events_user_fingerprint",
        ),
        sa.CheckConstraint(
            "outcome IN ('rejected', 'next_stage', 'unknown')",
            name="ck_application_email_events_outcome",
        ),
    )
    op.create_index(
        "ix_application_email_events_user_id",
        "application_email_events",
        ["user_id"],
    )
    op.create_index(
        "ix_application_email_events_application_id",
        "application_email_events",
        ["application_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_email_events_application_id",
        table_name="application_email_events",
    )
    op.drop_index(
        "ix_application_email_events_user_id",
        table_name="application_email_events",
    )
    op.drop_table("application_email_events")
