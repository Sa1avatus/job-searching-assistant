"""Add the source publication timestamp to vacancies."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vacancies",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vacancies_published_at", "vacancies", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_vacancies_published_at", table_name="vacancies")
    op.drop_column("vacancies", "published_at")
