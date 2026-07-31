from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.matching.backfill import MatchingBackfillService
from app.storage.database import Base
from app.storage.tables import (
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    EmbeddingRecordRow,
    UserRow,
    VacancyRow,
    WorkflowTaskRow,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_backfill_supports_dry_run_resume_and_idempotency() -> None:
    with _session() as session:
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
            analyzed_at=datetime.now(UTC),
        )
        session.add(cv_file)
        session.flush()
        for suffix in ("a", "b"):
            vacancy = VacancyRow(
                source_url=f"https://example.test/{suffix}",
                title="Engineer",
                company="Example",
                description_text="Python",
            )
            session.add(vacancy)
            session.flush()
            session.add(
                ApplicationRow(
                    user_id=user.id,
                    vacancy_id=vacancy.id,
                    selected_cv_file_id=cv_file.id,
                    status="awaiting_review",
                    match_score=50,
                )
            )
        session.commit()

        service = MatchingBackfillService(session)
        dry_run = service.schedule_applications(dry_run=True, limit=1, batch_size=1)
        report = service.schedule_applications(dry_run=False, limit=10, batch_size=1)
        repeated = service.schedule_applications(dry_run=False, limit=10, batch_size=2)

        assert dry_run.selected == 1
        assert dry_run.scheduled == 0
        assert report.scheduled == 2
        assert repeated.scheduled == 2
        assert session.query(WorkflowTaskRow).count() == 2


def test_index_metadata_consistency_reports_missing_evidence() -> None:
    with _session() as session:
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
        session.add(cv_file)
        session.flush()
        indexed = _evidence(user.id, cv_file.id, "Python")
        missing = _evidence(user.id, cv_file.id, "PostgreSQL")
        session.add_all((indexed, missing))
        session.flush()
        session.add(
            EmbeddingRecordRow(
                entity_type="candidate_evidence",
                entity_id=indexed.id,
                model_name="fake",
                model_revision="1",
                dimensions=3,
                normalization_method="l2",
                content_hash="b" * 64,
                index_name="evidence-v1",
                indexed_at=datetime.now(UTC),
            )
        )
        session.commit()

        report = MatchingBackfillService(session).verify_index_metadata()

        assert report.verified_evidence == 2
        assert report.indexed_evidence == 1
        assert report.missing_evidence_ids == (missing.id,)


def _evidence(user_id: str, cv_file_id: str, skill: str) -> CandidateEvidenceRow:
    return CandidateEvidenceRow(
        user_id=user_id,
        cv_file_id=cv_file_id,
        evidence_text=skill,
        normalized_text=skill.casefold(),
        evidence_type="hard_skill",
        skill_name=skill,
        experience_level="production",
        is_verified=True,
        source_fragment=skill,
        extraction_model="fake",
        extraction_model_version="1",
        extraction_schema_version="1",
        extraction_run_id="run-1",
        confidence=1,
    )
