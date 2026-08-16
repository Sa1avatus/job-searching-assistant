import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "site_definition_id",
            sa.String(36),
            sa.ForeignKey("site_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "vacancy_url_patterns",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "site_definition_id",
            "version",
            name="uq_workflow_definitions_site_version",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'testing', 'active', 'broken', 'archived')",
            name="ck_workflow_definitions_status",
        ),
    )
    op.create_index(
        "ix_workflow_definitions_site_definition_id",
        "workflow_definitions",
        ["site_definition_id"],
    )
    op.create_index(
        "ix_workflow_definitions_status",
        "workflow_definitions",
        ["status"],
    )

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "workflow_definition_id",
            sa.String(36),
            sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "action_type",
            sa.String(30),
            nullable=False,
        ),
        sa.Column(
            "selector_candidates",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "condition",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "parameters",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "timeout_ms",
            sa.Integer(),
            server_default=sa.text("10000"),
            nullable=False,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workflow_definition_id",
            "position",
            name="uq_workflow_steps_definition_position",
        ),
        sa.CheckConstraint(
            "action_type IN ('navigate', 'fill', 'upload', 'select', 'check', "
            "'click', 'wait', 'assert', 'human_review', 'submit')",
            name="ck_workflow_steps_action_type",
        ),
        sa.CheckConstraint(
            "timeout_ms >= 1 AND timeout_ms <= 120000",
            name="ck_workflow_steps_timeout_range",
        ),
    )
    op.create_index(
        "ix_workflow_steps_workflow_definition_id",
        "workflow_steps",
        ["workflow_definition_id"],
    )
    op.create_index(
        "ix_workflow_steps_action_type",
        "workflow_steps",
        ["action_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_steps_action_type",
        table_name="workflow_steps",
    )
    op.drop_index(
        "ix_workflow_steps_workflow_definition_id",
        table_name="workflow_steps",
    )
    op.drop_table("workflow_steps")

    op.drop_index(
        "ix_workflow_definitions_status",
        table_name="workflow_definitions",
    )
    op.drop_index(
        "ix_workflow_definitions_site_definition_id",
        table_name="workflow_definitions",
    )
    op.drop_table("workflow_definitions")
