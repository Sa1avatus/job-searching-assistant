"""Create site fields, mappings, and encrypted overrides."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_fields",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "site_definition_id",
            sa.String(36),
            sa.ForeignKey("site_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_key", sa.String(200), nullable=False),
        sa.Column("semantic_key", sa.String(200), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("field_type", sa.String(30), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "selector_candidates",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "site_definition_id",
            "field_key",
            name="uq_site_fields_definition_field_key",
        ),
    )
    op.create_index("ix_site_fields_site_definition_id", "site_fields", ["site_definition_id"])
    op.create_table(
        "site_field_mappings",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "site_field_id",
            sa.String(36),
            sa.ForeignKey("site_fields.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value_key", sa.String(200), nullable=False),
        sa.Column(
            "transformation",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "site_field_id",
            name="uq_site_field_mappings_site_field_id",
        ),
    )
    op.create_index(
        "ix_site_field_mappings_site_field_id",
        "site_field_mappings",
        ["site_field_id"],
    )
    op.create_table(
        "site_value_overrides",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "site_definition_id",
            sa.String(36),
            sa.ForeignKey("site_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "site_field_id",
            sa.String(36),
            sa.ForeignKey("site_fields.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope_key", sa.String(36), nullable=False),
        sa.Column("value_key", sa.String(200), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "site_definition_id",
            "scope_key",
            "value_key",
            name="uq_site_value_overrides_scope_value_key",
        ),
    )
    op.create_index(
        "ix_site_value_overrides_site_definition_id",
        "site_value_overrides",
        ["site_definition_id"],
    )
    op.create_index(
        "ix_site_value_overrides_site_field_id",
        "site_value_overrides",
        ["site_field_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_value_overrides_site_field_id",
        table_name="site_value_overrides",
    )
    op.drop_index(
        "ix_site_value_overrides_site_definition_id",
        table_name="site_value_overrides",
    )
    op.drop_table("site_value_overrides")
    op.drop_index(
        "ix_site_field_mappings_site_field_id",
        table_name="site_field_mappings",
    )
    op.drop_table("site_field_mappings")
    op.drop_index("ix_site_fields_site_definition_id", table_name="site_fields")
    op.drop_table("site_fields")
