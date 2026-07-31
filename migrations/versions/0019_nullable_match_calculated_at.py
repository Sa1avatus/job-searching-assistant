"""Allow pending matching results without a completion timestamp."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "application_match_results",
        "calculated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE application_match_results "
        "SET calculated_at = COALESCE(updated_at, CURRENT_TIMESTAMP) "
        "WHERE calculated_at IS NULL"
    )
    op.alter_column(
        "application_match_results",
        "calculated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
