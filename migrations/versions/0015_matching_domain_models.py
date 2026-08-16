"""Add versioned, explainable matching persistence."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vacancy_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "vacancy_id",
            sa.String(36),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("requirement_type", sa.String(50), nullable=False),
        sa.Column("importance", sa.String(50), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("is_blocker", sa.Boolean(), nullable=False),
        sa.Column("alternatives_json", sa.JSON(), nullable=False),
        sa.Column("source_fragment", sa.Text(), nullable=False),
        sa.Column("source_section", sa.String(200), nullable=True),
        sa.Column("extraction_model", sa.String(200), nullable=False),
        sa.Column("extraction_model_version", sa.String(200), nullable=False),
        sa.Column("extraction_schema_version", sa.String(100), nullable=False),
        sa.Column("extraction_run_id", sa.String(36), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("weight >= 0", name="ck_vacancy_requirements_weight_nonnegative"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_vacancy_requirements_confidence_range",
        ),
        sa.UniqueConstraint(
            "vacancy_id",
            "extraction_run_id",
            "normalized_text",
            "requirement_type",
            name="uq_vacancy_requirements_run_normalized_type",
        ),
    )
    op.create_index("ix_vacancy_requirements_vacancy_id", "vacancy_requirements", ["vacancy_id"])
    op.create_index(
        "ix_vacancy_requirements_vacancy_type_importance",
        "vacancy_requirements",
        ["vacancy_id", "requirement_type", "importance"],
    )

    op.create_table(
        "candidate_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cv_file_id",
            sa.String(36),
            sa.ForeignKey("cv_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column("skill_name", sa.String(200), nullable=True),
        sa.Column("experience_level", sa.String(50), nullable=False),
        sa.Column("years", sa.Float(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("source_fragment", sa.Text(), nullable=False),
        sa.Column("source_section", sa.String(200), nullable=True),
        sa.Column("extraction_model", sa.String(200), nullable=False),
        sa.Column("extraction_model_version", sa.String(200), nullable=False),
        sa.Column("extraction_schema_version", sa.String(100), nullable=False),
        sa.Column("extraction_run_id", sa.String(36), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_candidate_evidence_confidence_range",
        ),
        sa.CheckConstraint(
            "years IS NULL OR years >= 0",
            name="ck_candidate_evidence_years_nonnegative",
        ),
        sa.UniqueConstraint(
            "cv_file_id",
            "extraction_run_id",
            "normalized_text",
            "evidence_type",
            name="uq_candidate_evidence_run_normalized_type",
        ),
    )
    op.create_index("ix_candidate_evidence_user_id", "candidate_evidence", ["user_id"])
    op.create_index("ix_candidate_evidence_cv_file_id", "candidate_evidence", ["cv_file_id"])
    op.create_index(
        "ix_candidate_evidence_cv_type_experience",
        "candidate_evidence",
        ["cv_file_id", "evidence_type", "experience_level"],
    )
    op.create_index(
        "ix_candidate_evidence_user_cv", "candidate_evidence", ["user_id", "cv_file_id"]
    )

    op.create_table(
        "embedding_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_revision", sa.String(200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("normalization_method", sa.String(50), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("index_name", sa.String(255), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dimensions > 0", name="ck_embedding_records_dimensions_positive"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "model_name",
            "model_revision",
            "content_hash",
            name="uq_embedding_records_entity_model_content",
        ),
    )
    op.create_index(
        "ix_embedding_records_entity", "embedding_records", ["entity_type", "entity_id"]
    )

    op.create_table(
        "application_match_results",
        sa.Column(
            "application_id",
            sa.String(36),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "cv_file_id",
            sa.String(36),
            sa.ForeignKey("cv_files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("eligibility_status", sa.String(50), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("hard_skill_score", sa.Float(), nullable=False),
        sa.Column("preferred_skill_score", sa.Float(), nullable=False),
        sa.Column("role_score", sa.Float(), nullable=False),
        sa.Column("seniority_score", sa.Float(), nullable=False),
        sa.Column("experience_score", sa.Float(), nullable=False),
        sa.Column("work_format_score", sa.Float(), nullable=False),
        sa.Column("location_score", sa.Float(), nullable=False),
        sa.Column("domain_score", sa.Float(), nullable=False),
        sa.Column("blocker_count", sa.Integer(), nullable=False),
        sa.Column("matched_required_count", sa.Integer(), nullable=False),
        sa.Column("missing_required_count", sa.Integer(), nullable=False),
        sa.Column("scoring_version", sa.String(100), nullable=False),
        sa.Column("model_versions_json", sa.JSON(), nullable=False),
        sa.Column("explanation_json", sa.JSON(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "final_score >= 0 AND final_score <= 100",
            name="ck_application_match_results_final_range",
        ),
    )
    op.create_index(
        "ix_application_match_results_cv_file_id",
        "application_match_results",
        ["cv_file_id"],
    )

    op.create_table(
        "requirement_matches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            sa.String(36),
            sa.ForeignKey("vacancy_requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.String(36),
            sa.ForeignKey("candidate_evidence.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("lexical_score", sa.Float(), nullable=True),
        sa.Column("dense_score", sa.Float(), nullable=True),
        sa.Column("hybrid_score", sa.Float(), nullable=True),
        sa.Column("reranker_raw_score", sa.Float(), nullable=True),
        sa.Column("reranker_score", sa.Float(), nullable=True),
        sa.Column("final_match_score", sa.Float(), nullable=False),
        sa.Column("match_level", sa.String(50), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("retrieval_model_versions_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lexical_score IS NULL OR (lexical_score >= 0 AND lexical_score <= 1)",
            name="ck_requirement_matches_lexical_range",
        ),
        sa.CheckConstraint(
            "dense_score IS NULL OR (dense_score >= 0 AND dense_score <= 1)",
            name="ck_requirement_matches_dense_range",
        ),
        sa.CheckConstraint(
            "hybrid_score IS NULL OR (hybrid_score >= 0 AND hybrid_score <= 1)",
            name="ck_requirement_matches_hybrid_range",
        ),
        sa.CheckConstraint(
            "reranker_score IS NULL OR (reranker_score >= 0 AND reranker_score <= 1)",
            name="ck_requirement_matches_reranker_range",
        ),
        sa.CheckConstraint(
            "final_match_score >= 0 AND final_match_score <= 100",
            name="ck_requirement_matches_final_range",
        ),
        sa.UniqueConstraint(
            "application_id",
            "requirement_id",
            name="uq_requirement_matches_application_requirement",
        ),
    )
    op.create_index(
        "ix_requirement_matches_application_id", "requirement_matches", ["application_id"]
    )
    op.create_index(
        "ix_requirement_matches_requirement_id", "requirement_matches", ["requirement_id"]
    )
    op.create_index("ix_requirement_matches_evidence_id", "requirement_matches", ["evidence_id"])


def downgrade() -> None:
    op.drop_table("requirement_matches")
    op.drop_table("application_match_results")
    op.drop_table("embedding_records")
    op.drop_table("candidate_evidence")
    op.drop_table("vacancy_requirements")
