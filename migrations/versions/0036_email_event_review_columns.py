"""Store email category, confidence, content, and review state on email events.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen the outcome constraint to admit `offer` (the LLM classifier can now emit it).
    op.drop_constraint(
        "ck_application_email_events_outcome",
        "application_email_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_application_email_events_outcome",
        "application_email_events",
        "outcome IN ('rejected', 'next_stage', 'offer', 'unknown')",
    )

    # Email review fields: the review queue needs the email content (subject/body) to let
    # the user decide which vacancy an ambiguous message belongs to, plus the classifier's
    # category/confidence and the candidate vacancies considered.
    op.add_column(
        "application_email_events",
        sa.Column("category", sa.String(30), nullable=True),
    )
    op.add_column(
        "application_email_events",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "application_email_events",
        sa.Column("subject", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_email_events",
        sa.Column("body", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_email_events",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "application_email_events",
        sa.Column("candidates", sa.JSON(), nullable=True),
    )
    op.create_check_constraint(
        "ck_application_email_events_category",
        "application_email_events",
        "category IS NULL OR category IN ("
        "'application_received', 'recruiter_contact', 'question', 'test_assignment', "
        "'interview_invitation', 'interview_reschedule', 'offer', 'rejection', "
        "'follow_up', 'other')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_application_email_events_category",
        "application_email_events",
        type_="check",
    )
    op.drop_column("application_email_events", "candidates")
    op.drop_column("application_email_events", "needs_review")
    op.drop_column("application_email_events", "body")
    op.drop_column("application_email_events", "subject")
    op.drop_column("application_email_events", "confidence")
    op.drop_column("application_email_events", "category")

    op.drop_constraint(
        "ck_application_email_events_outcome",
        "application_email_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_application_email_events_outcome",
        "application_email_events",
        "outcome IN ('rejected', 'next_stage', 'unknown')",
    )
