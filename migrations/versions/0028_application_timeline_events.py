import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_timeline_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("previous_value", sa.String(200), nullable=True),
        sa.Column("new_value", sa.String(200), nullable=True),
        sa.Column("detail_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "source", sa.String(100), server_default=sa.text("'system'"), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ("
            "'status_change', 'email_received', 'email_sent', "
            "'note_added', 'match_calculated', 'manual_update'"
            ")",
            name="ck_application_timeline_events_event_type",
        ),
    )
    op.create_index(
        "ix_application_timeline_events_app_occurred",
        "application_timeline_events",
        ["application_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_application_timeline_events_app_occurred",
        table_name="application_timeline_events",
    )
    op.drop_table("application_timeline_events")
