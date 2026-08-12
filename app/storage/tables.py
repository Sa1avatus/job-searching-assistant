from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    active_cv_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("cv_files.id", ondelete="SET NULL", use_alter=True), nullable=True, index=True
    )
    facts: Mapped[list[ProfileFactRow]] = relationship(cascade="all, delete-orphan")


class LlmPreferenceRow(Base):
    __tablename__ = "llm_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EmailIntegrationRow(Base):
    __tablename__ = "email_integrations"
    __table_args__ = (
        CheckConstraint(
            "port >= 1 AND port <= 65535",
            name="ck_email_integrations_port_range",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=993)
    username: Mapped[str] = mapped_column(String(320))
    encrypted_password: Mapped[str] = mapped_column(Text)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    mailbox: Mapped[str] = mapped_column(String(255), default="INBOX")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class CompanyBlacklistRow(Base):
    __tablename__ = "company_blacklist"
    __table_args__ = (UniqueConstraint("user_id", "normalized_company"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(300))
    normalized_company: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CvFileRow(Base):
    __tablename__ = "cv_files"
    __table_args__ = (UniqueConstraint("user_id", "sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(150))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_summary: Mapped[str] = mapped_column(Text, default="")
    search_keywords: Mapped[str] = mapped_column(Text, default="")
    years_of_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProfileFactRow(Base):
    __tablename__ = "profile_facts"
    __table_args__ = (UniqueConstraint("user_id", "category", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AutofillValueRow(Base):
    __tablename__ = "autofill_values"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_autofill_values_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(200))
    value_type: Mapped[str] = mapped_column(String(30))
    encrypted_value: Mapped[str] = mapped_column(Text)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SiteDefinitionRow(Base):
    __tablename__ = "site_definitions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "site_key",
            name="uq_site_definitions_user_site_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    site_key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    login_url: Mapped[str] = mapped_column(Text)
    allowed_hosts: Mapped[list[str]] = mapped_column(JSON, default=list)
    authorization_rules: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class WorkflowDefinitionRow(Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint(
            "site_definition_id",
            "version",
            name="uq_workflow_definitions_site_version",
        ),
        CheckConstraint(
            "status IN ('draft', 'testing', 'active', 'broken', 'archived')",
            name="ck_workflow_definitions_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    site_definition_id: Mapped[str] = mapped_column(
        ForeignKey("site_definitions.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    vacancy_url_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class WorkflowStepRow(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint(
            "workflow_definition_id",
            "position",
            name="uq_workflow_steps_definition_position",
        ),
        CheckConstraint(
            "action_type IN ('navigate', 'fill', 'upload', 'select', 'check', "
            "'click', 'wait', 'assert', 'human_review', 'submit')",
            name="ck_workflow_steps_action_type",
        ),
        CheckConstraint(
            "timeout_ms >= 1 AND timeout_ms <= 120000",
            name="ck_workflow_steps_timeout_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_definition_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(30), index=True)
    selector_candidates: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    condition: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=10_000)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SiteFieldRow(Base):
    __tablename__ = "site_fields"
    __table_args__ = (
        UniqueConstraint(
            "site_definition_id",
            "field_key",
            name="uq_site_fields_definition_field_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    site_definition_id: Mapped[str] = mapped_column(
        ForeignKey("site_definitions.id", ondelete="CASCADE"), index=True
    )
    field_key: Mapped[str] = mapped_column(String(200))
    semantic_key: Mapped[str] = mapped_column(String(200), default="custom")
    label: Mapped[str] = mapped_column(String(300), default="")
    field_type: Mapped[str] = mapped_column(String(30))
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    selector_candidates: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SiteFieldMappingRow(Base):
    __tablename__ = "site_field_mappings"
    __table_args__ = (
        UniqueConstraint(
            "site_field_id",
            name="uq_site_field_mappings_site_field_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    site_field_id: Mapped[str] = mapped_column(
        ForeignKey("site_fields.id", ondelete="CASCADE"), index=True
    )
    value_key: Mapped[str] = mapped_column(String(200))
    transformation: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SiteValueOverrideRow(Base):
    __tablename__ = "site_value_overrides"
    __table_args__ = (
        UniqueConstraint(
            "site_definition_id",
            "scope_key",
            "value_key",
            name="uq_site_value_overrides_scope_value_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    site_definition_id: Mapped[str] = mapped_column(
        ForeignKey("site_definitions.id", ondelete="CASCADE"), index=True
    )
    site_field_id: Mapped[str | None] = mapped_column(
        ForeignKey("site_fields.id", ondelete="CASCADE"), nullable=True, index=True
    )
    scope_key: Mapped[str] = mapped_column(String(36))
    value_key: Mapped[str] = mapped_column(String(200))
    encrypted_value: Mapped[str] = mapped_column(Text)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class VacancyRow(Base):
    __tablename__ = "vacancies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300))
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    location: Mapped[str] = mapped_column(String(300), default="")
    description_text: Mapped[str] = mapped_column(Text, default="")
    adapter_name: Mapped[str] = mapped_column(String(50), default="generic")
    source_evidence_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_fields: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    requires_sensitive_review: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    salary_text: Mapped[str] = mapped_column(Text, default="")
    work_format: Mapped[str] = mapped_column(String(30), default="unspecified", index=True)
    employment_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VacancyRequirementRow(Base):
    __tablename__ = "vacancy_requirements"
    __table_args__ = (
        UniqueConstraint(
            "vacancy_id",
            "extraction_run_id",
            "normalized_text",
            "requirement_type",
            name="uq_vacancy_requirements_run_normalized_type",
        ),
        Index(
            "ix_vacancy_requirements_vacancy_type_importance",
            "vacancy_id",
            "requirement_type",
            "importance",
        ),
        CheckConstraint("weight >= 0", name="ck_vacancy_requirements_weight_nonnegative"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_vacancy_requirements_confidence_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    vacancy_id: Mapped[str] = mapped_column(
        ForeignKey("vacancies.id", ondelete="CASCADE"), index=True
    )
    requirement_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    requirement_type: Mapped[str] = mapped_column(String(50))
    importance: Mapped[str] = mapped_column(String(50), default="unknown")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    is_blocker: Mapped[bool] = mapped_column(Boolean, default=False)
    alternatives_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_fragment: Mapped[str] = mapped_column(Text)
    source_section: Mapped[str | None] = mapped_column(String(200), nullable=True)
    extraction_model: Mapped[str] = mapped_column(String(200))
    extraction_model_version: Mapped[str] = mapped_column(String(200))
    extraction_schema_version: Mapped[str] = mapped_column(String(100))
    extraction_run_id: Mapped[str] = mapped_column(String(36))
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class CandidateEvidenceRow(Base):
    __tablename__ = "candidate_evidence"
    __table_args__ = (
        UniqueConstraint(
            "cv_file_id",
            "extraction_run_id",
            "normalized_text",
            "evidence_type",
            name="uq_candidate_evidence_run_normalized_type",
        ),
        Index(
            "ix_candidate_evidence_cv_type_experience",
            "cv_file_id",
            "evidence_type",
            "experience_level",
        ),
        Index("ix_candidate_evidence_user_cv", "user_id", "cv_file_id"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_candidate_evidence_confidence_range",
        ),
        CheckConstraint(
            "years IS NULL OR years >= 0",
            name="ck_candidate_evidence_years_nonnegative",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cv_file_id: Mapped[str] = mapped_column(
        ForeignKey("cv_files.id", ondelete="CASCADE"), index=True
    )
    evidence_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(50))
    skill_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    experience_level: Mapped[str] = mapped_column(String(50), default="unknown")
    years: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_fragment: Mapped[str] = mapped_column(Text)
    source_section: Mapped[str | None] = mapped_column(String(200), nullable=True)
    extraction_model: Mapped[str] = mapped_column(String(200))
    extraction_model_version: Mapped[str] = mapped_column(String(200))
    extraction_schema_version: Mapped[str] = mapped_column(String(100))
    extraction_run_id: Mapped[str] = mapped_column(String(36))
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class EmbeddingRecordRow(Base):
    __tablename__ = "embedding_records"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "model_name",
            "model_revision",
            "content_hash",
            name="uq_embedding_records_entity_model_content",
        ),
        Index("ix_embedding_records_entity", "entity_type", "entity_id"),
        CheckConstraint("dimensions > 0", name="ck_embedding_records_dimensions_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36))
    model_name: Mapped[str] = mapped_column(String(200))
    model_revision: Mapped[str] = mapped_column(String(200))
    dimensions: Mapped[int] = mapped_column(Integer)
    normalization_method: Mapped[str] = mapped_column(String(50))
    content_hash: Mapped[str] = mapped_column(String(64))
    index_name: Mapped[str] = mapped_column(String(255))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApplicationRow(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "vacancy_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    vacancy_id: Mapped[str] = mapped_column(ForeignKey("vacancies.id"), index=True)
    selected_cv_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("cv_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="draft")
    match_score: Mapped[int] = mapped_column(Integer)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    cover_letter_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    answers: Mapped[list[ApplicationAnswerRow]] = relationship(cascade="all, delete-orphan")


class ApplicationEmailEventRow(Base):
    __tablename__ = "application_email_events"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "message_fingerprint",
            name="uq_application_email_events_user_fingerprint",
        ),
        CheckConstraint(
            "outcome IN ('rejected', 'next_stage', 'unknown')",
            name="ck_application_email_events_outcome",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_fingerprint: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(30))
    status_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApplicationTimelineEventRow(Base):
    __tablename__ = "application_timeline_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ("
            "'status_change', 'email_received', 'email_sent', "
            "'note_added', 'match_calculated', 'manual_update'"
            ")",
            name="ck_application_timeline_events_event_type",
        ),
        Index(
            "ix_application_timeline_events_app_occurred",
            "application_id",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    previous_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(100), default="system")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RequirementMatchRow(Base):
    __tablename__ = "requirement_matches"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "requirement_id",
            name="uq_requirement_matches_application_requirement",
        ),
        CheckConstraint(
            "lexical_score IS NULL OR (lexical_score >= 0 AND lexical_score <= 1)",
            name="ck_requirement_matches_lexical_range",
        ),
        CheckConstraint(
            "dense_score IS NULL OR (dense_score >= 0 AND dense_score <= 1)",
            name="ck_requirement_matches_dense_range",
        ),
        CheckConstraint(
            "hybrid_score IS NULL OR (hybrid_score >= 0 AND hybrid_score <= 1)",
            name="ck_requirement_matches_hybrid_range",
        ),
        CheckConstraint(
            "reranker_score IS NULL OR (reranker_score >= 0 AND reranker_score <= 1)",
            name="ck_requirement_matches_reranker_range",
        ),
        CheckConstraint(
            "final_match_score >= 0 AND final_match_score <= 100",
            name="ck_requirement_matches_final_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("vacancy_requirements.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_evidence.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lexical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    dense_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hybrid_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reranker_raw_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reranker_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_match_score: Mapped[float] = mapped_column(Float)
    match_level: Mapped[str] = mapped_column(String(50))
    explanation: Mapped[str] = mapped_column(Text)
    retrieval_model_versions_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApplicationMatchResultRow(Base):
    __tablename__ = "application_match_results"
    __table_args__ = (
        CheckConstraint(
            "final_score >= 0 AND final_score <= 100",
            name="ck_application_match_results_final_range",
        ),
        CheckConstraint(
            "language_score >= 0 AND language_score <= 100",
            name="ck_application_match_results_language_range",
        ),
        CheckConstraint(
            "semantic_similarity >= 0 AND semantic_similarity <= 1",
            name="ck_application_match_results_semantic_range",
        ),
        CheckConstraint(
            "reranker_score >= 0 AND reranker_score <= 1",
            name="ck_application_match_results_reranker_range",
        ),
        CheckConstraint(
            "requirements_match >= 0 AND requirements_match <= 100",
            name="ck_application_match_results_requirements_range",
        ),
    )

    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    cv_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("cv_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    eligibility_status: Mapped[str] = mapped_column(String(50), default="pending")
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    hard_skill_score: Mapped[float] = mapped_column(Float, default=0.0)
    preferred_skill_score: Mapped[float] = mapped_column(Float, default=0.0)
    role_score: Mapped[float] = mapped_column(Float, default=0.0)
    seniority_score: Mapped[float] = mapped_column(Float, default=0.0)
    experience_score: Mapped[float] = mapped_column(Float, default=0.0)
    work_format_score: Mapped[float] = mapped_column(Float, default=0.0)
    location_score: Mapped[float] = mapped_column(Float, default=0.0)
    domain_score: Mapped[float] = mapped_column(Float, default=0.0)
    language_score: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    reranker_score: Mapped[float] = mapped_column(Float, default=0.0)
    requirements_match: Mapped[float] = mapped_column(Float, default=0.0)
    blocker_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_required_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_required_count: Mapped[int] = mapped_column(Integer, default=0)
    scoring_version: Mapped[str] = mapped_column(String(100), default="pending")
    model_versions_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    explanation_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class ApplicationAnswerRow(Base):
    __tablename__ = "application_answers"
    __table_args__ = (UniqueConstraint("application_id", "field_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    field_id: Mapped[str] = mapped_column(String(300))
    label: Mapped[str] = mapped_column(Text)
    semantic_category: Mapped[str] = mapped_column(String(100), default="custom")
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_source: Mapped[str] = mapped_column(String(50), default="missing")
    source_fact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class WorkflowTaskRow(Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (
        Index("ix_workflow_tasks_queue_claim", "queue_name", "state", "scheduled_for"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(300), unique=True)
    queue_name: Mapped[str] = mapped_column(String(50), default="dispatcher", index=True)
    task_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(50), default="pending")
    attempt_number: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
    transitions: Mapped[list[TaskTransitionRow]] = relationship(cascade="all, delete-orphan")
    human_actions: Mapped[list[HumanActionCheckpointRow]] = relationship(
        cascade="all, delete-orphan"
    )


class HumanActionCheckpointRow(Base):
    __tablename__ = "human_action_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(50), default="waiting", index=True)
    instructions: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    resolution_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifacts: Mapped[list[EvidenceArtifactRow]] = relationship(cascade="all, delete-orphan")


class EvidenceArtifactRow(Base):
    __tablename__ = "evidence_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    checkpoint_id: Mapped[str] = mapped_column(
        ForeignKey("human_action_checkpoints.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(50))
    relative_path: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BrowserSessionRow(Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (UniqueConstraint("user_id", "site_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    site_key: Mapped[str] = mapped_column(String(100), index=True)
    adapter_name: Mapped[str] = mapped_column(String(100))
    encrypted_state_path: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(50), default="available", index=True)
    last_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_restored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskTransitionRow(Base):
    __tablename__ = "task_transitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"), index=True
    )
    previous_state: Mapped[str] = mapped_column(String(50))
    new_state: Mapped[str] = mapped_column(String(50))
    reason: Mapped[str] = mapped_column(Text)
    worker: Mapped[str] = mapped_column(String(200))
    attempt_number: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkerHeartbeatRow(Base):
    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    status: Mapped[str] = mapped_column(String(50))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
