"""Ledger of real submission attempts (idempotent, crash-safe submission).

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-19

Backfill: every application already ``submitted`` gets a ``confirmed`` ledger row
(verified_by='legacy') so nothing that was sent before this migration can be submitted again.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("site_key", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("verified_by", sa.String(30), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('attempting', 'unknown', 'confirmed', 'failed')",
            name="ck_application_submissions_state",
        ),
    )
    op.create_index(
        "uq_application_submissions_open",
        "application_submissions",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('attempting', 'unknown', 'confirmed')"),
        sqlite_where=sa.text("state IN ('attempting', 'unknown', 'confirmed')"),
    )

    bind = op.get_bind()
    applications = bind.execute(
        sa.text(
            "SELECT a.id, a.user_id, v.adapter_name FROM applications a "
            "JOIN vacancies v ON v.id = a.vacancy_id WHERE a.status IN "
            "('submitted', 'interview', 'offer')"
        )
    ).all()
    ledger = sa.table(
        "application_submissions",
        sa.column("id", sa.String),
        sa.column("application_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("site_key", sa.String),
        sa.column("state", sa.String),
        sa.column("verified_by", sa.String),
        sa.column("evidence", sa.JSON),
    )
    for application_id, user_id, adapter_name in applications:
        bind.execute(
            ledger.insert().values(
                id=str(uuid.uuid4()),
                application_id=application_id,
                user_id=user_id,
                site_key=adapter_name or "unknown",
                state="confirmed",
                verified_by="legacy",
                evidence=["backfilled from application status"],
            )
        )


def downgrade() -> None:
    op.drop_index("uq_application_submissions_open", table_name="application_submissions")
    op.drop_table("application_submissions")
