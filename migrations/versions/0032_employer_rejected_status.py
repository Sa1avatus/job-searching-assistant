"""Separate employer rejection from a user-rejected vacancy.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE applications
            SET status = 'employer_rejected'
            WHERE status = 'rejected'
              AND EXISTS (
                  SELECT 1
                  FROM application_email_events
                  WHERE application_email_events.application_id = applications.id
                    AND application_email_events.outcome = 'rejected'
                    AND application_email_events.status_applied = true
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE application_timeline_events
            SET new_value = 'employer_rejected'
            WHERE event_type = 'status_change'
              AND source = 'email_event'
              AND new_value = 'rejected'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE application_timeline_events
            SET new_value = 'rejected'
            WHERE event_type = 'status_change'
              AND source = 'email_event'
              AND new_value = 'employer_rejected'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE applications
            SET status = 'rejected'
            WHERE status = 'employer_rejected'
            """
        )
    )
