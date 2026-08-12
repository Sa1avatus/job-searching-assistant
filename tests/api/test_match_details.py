from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.config import get_settings
from app.storage.database import Base, session_scope
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    RequirementMatchRow,
    UserRow,
    VacancyRequirementRow,
    VacancyRow,
)


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_match_details_returns_explainable_shadow_result() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        cv_file = CvFileRow(
            user_id=user.id,
            original_filename="resume.txt",
            storage_path="resume.txt",
            content_type="text/plain",
            sha256="a" * 64,
            size_bytes=10,
        )
        vacancy = VacancyRow(
            source_url="https://example.test/vacancy",
            title="Engineer",
            company="Example",
        )
        session.add_all((cv_file, vacancy))
        session.flush()
        application = ApplicationRow(
            user_id=user.id,
            vacancy_id=vacancy.id,
            selected_cv_file_id=cv_file.id,
            status="awaiting_review",
            match_score=55,
        )
        requirement = VacancyRequirementRow(
            vacancy_id=vacancy.id,
            requirement_text="Python production experience",
            normalized_text="python production experience",
            requirement_type="hard_skill",
            importance="required",
            weight=1,
            is_blocker=False,
            alternatives_json=[],
            source_fragment="Python production experience",
            extraction_model="fake",
            extraction_model_version="1",
            extraction_schema_version="1",
            extraction_run_id="run-1",
            confidence=1,
        )
        evidence = CandidateEvidenceRow(
            user_id=user.id,
            cv_file_id=cv_file.id,
            evidence_text="Built Python services",
            normalized_text="python services",
            evidence_type="work_experience",
            skill_name="Python",
            experience_level="production",
            is_verified=True,
            source_fragment="Built Python services",
            extraction_model="fake",
            extraction_model_version="1",
            extraction_schema_version="1",
            extraction_run_id="run-1",
            confidence=1,
        )
        session.add_all((application, requirement, evidence))
        session.flush()
        session.add_all(
            (
                RequirementMatchRow(
                    application_id=application.id,
                    requirement_id=requirement.id,
                    evidence_id=evidence.id,
                    lexical_score=0.8,
                    dense_score=0.9,
                    hybrid_score=0.85,
                    reranker_raw_score=2.1,
                    reranker_score=0.9,
                    final_match_score=90,
                    match_level="strong",
                    explanation="Production evidence supports the requirement",
                    retrieval_model_versions_json={"reranker": {"name": "fake"}},
                ),
                ApplicationMatchResultRow(
                    application_id=application.id,
                    status="scored",
                    cv_file_id=cv_file.id,
                    eligibility_status="eligible",
                    final_score=90,
                    hard_skill_score=90,
                    preferred_skill_score=0,
                    role_score=0,
                    seniority_score=0,
                    experience_score=0,
                    work_format_score=0,
                    location_score=0,
                    domain_score=0,
                    language_score=0,
                    semantic_similarity=0.85,
                    reranker_score=0.9,
                    requirements_match=100.0,
                    blocker_count=0,
                    matched_required_count=1,
                    missing_required_count=0,
                    scoring_version="matching-v2.1",
                    model_versions_json={"reranker": {"name": "fake"}},
                    explanation_json={"summary": ["Supported"]},
                    calculated_at=datetime.now(UTC),
                ),
            )
        )
        session.commit()
        application_id = application.id

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.get(f"/v1/applications/{application_id}/match-details")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["legacy_match_score"] == 55
    assert payload["final_score"] == 90
    assert payload["language_score"] == 0
    assert payload["semantic_similarity"] == 0.85
    assert payload["reranker_score"] == 0.9
    assert payload["requirements_match"] == 100.0
    assert payload["requirements"][0]["match_level"] == "strong"
    assert payload["requirements"][0]["evidence_experience_level"] == "production"


def test_match_details_returns_not_calculated_for_legacy_application() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        user = UserRow(display_name="Candidate")
        vacancy = VacancyRow(
            source_url="https://example.test/legacy",
            title="Engineer",
            company="Example",
        )
        session.add_all((user, vacancy))
        session.flush()
        application = ApplicationRow(
            user_id=user.id,
            vacancy_id=vacancy.id,
            status="awaiting_review",
            match_score=50,
        )
        session.add(application)
        session.commit()
        application_id = application.id

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.get(f"/v1/applications/{application_id}/match-details")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Match details have not been calculated"


def test_recalculate_match_schedules_idempotent_background_task(monkeypatch) -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        cv_file = CvFileRow(
            user_id=user.id,
            original_filename="resume.txt",
            storage_path="resume.txt",
            content_type="text/plain",
            sha256="b" * 64,
            size_bytes=10,
            analyzed_at=datetime.now(UTC),
        )
        vacancy = VacancyRow(
            source_url="https://example.test/recalculate",
            title="Engineer",
            company="Example",
            description_text="Python is required",
        )
        session.add_all((cv_file, vacancy))
        session.flush()
        application = ApplicationRow(
            user_id=user.id,
            vacancy_id=vacancy.id,
            selected_cv_file_id=cv_file.id,
            status="awaiting_review",
            match_score=50,
        )
        session.add(application)
        session.commit()
        application_id = application.id

    monkeypatch.setenv("APP_MATCHING_V2_ENABLED", "true")
    get_settings.cache_clear()
    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            first = client.post(f"/v1/applications/{application_id}/recalculate-match")
            second = client.post(f"/v1/applications/{application_id}/recalculate-match")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["state"] == "scheduled"
