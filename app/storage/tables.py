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
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
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
