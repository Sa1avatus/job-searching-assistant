"""Add adapter evidence and form schema to vacancies."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vacancies", sa.Column("location", sa.String(300), nullable=False, server_default="")
    )
    op.add_column(
        "vacancies", sa.Column("description_text", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "vacancies",
        sa.Column("adapter_name", sa.String(50), nullable=False, server_default="generic"),
    )
    op.add_column("vacancies", sa.Column("source_evidence_url", sa.Text(), nullable=True))
    op.add_column(
        "vacancies", sa.Column("application_fields", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "vacancies",
        sa.Column(
            "requires_sensitive_review", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    for column_name in (
        "requires_sensitive_review",
        "application_fields",
        "source_evidence_url",
        "adapter_name",
        "description_text",
        "location",
    ):
        op.drop_column("vacancies", column_name)
