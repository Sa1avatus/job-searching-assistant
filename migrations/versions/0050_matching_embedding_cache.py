"""Vector cache for the shadow scorer (app.matching.shadow_scorer): vacancy/resume e5
embeddings keyed by content hash + model identity, so unchanged text isn't re-embedded.

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-25

No backfill: an empty table just means every shadow-mode lookup is a cache miss until it's
populated by live calls.
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matching_embedding_cache",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_revision", sa.String(200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "entity_type IN ('vacancy', 'resume')", name="ck_matching_embedding_cache_entity_type"
        ),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "model_name",
            "model_revision",
            "content_hash",
            name="uq_matching_embedding_cache_entity_model_content",
        ),
    )
    op.create_index(
        "ix_matching_embedding_cache_entity",
        "matching_embedding_cache",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_matching_embedding_cache_entity", table_name="matching_embedding_cache")
    op.drop_table("matching_embedding_cache")
