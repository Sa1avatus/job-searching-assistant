import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_integrations",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), server_default=sa.text("993"), nullable=False),
        sa.Column("username", sa.String(320), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("use_ssl", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "mailbox",
            sa.String(255),
            server_default=sa.text("'INBOX'"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "port >= 1 AND port <= 65535",
            name="ck_email_integrations_port_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("email_integrations")
