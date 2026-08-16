"""Add is_unresolved_blocker to requirement_matches

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("requirement_matches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_unresolved_blocker",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("requirement_matches") as batch_op:
        batch_op.drop_column("is_unresolved_blocker")
