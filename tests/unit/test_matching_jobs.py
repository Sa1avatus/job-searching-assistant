from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.matching.jobs import MatchingJobNotReadyError, MatchingJobService
from app.storage.database import Base
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    UserRow,
    VacancyRow,
    WorkflowTaskRow,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_matching_job_is_idempotent_and_contains_no_cv_text() -> None:
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
            size_bytes=12,
            analyzed_at=datetime.now(UTC),
        )
        vacancy = VacancyRow(
            source_url="https://example.test/job",
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

        first = MatchingJobService(session).schedule(application.id)
        second = MatchingJobService(session).schedule(application.id)

        assert first.id == second.id
        forced = MatchingJobService(session).schedule(application.id, force=True)
        assert forced.id == first.id
        assert session.query(WorkflowTaskRow).count() == 1
        assert first.queue_name == "matching"
        assert first.priority == 100
        assert "Python is required" not in str(first.task_payload)
        assert set(first.task_payload) == {
            "application_id",
            "cv_file_id",
            "content_version",
        }
        aggregate = session.get(ApplicationMatchResultRow, application.id)
        assert aggregate is not None
        assert aggregate.status == "pending"


def test_matching_job_requires_selected_cv() -> None:
    with _session() as session:
        user = UserRow(display_name="Candidate")
        vacancy = VacancyRow(
            source_url="https://example.test/job",
            title="Engineer",
            company="Example",
        )
        session.add_all((user, vacancy))
        session.flush()
        application = ApplicationRow(
            user_id=user.id,
            vacancy_id=vacancy.id,
            selected_cv_file_id=None,
            status="awaiting_review",
            match_score=0,
        )
        session.add(application)
        session.commit()

        try:
            MatchingJobService(session).schedule(application.id)
        except MatchingJobNotReadyError as error:
            assert "Select a CV" in str(error)
        else:
            raise AssertionError("Expected selected-CV validation")

        assert session.scalar(select(WorkflowTaskRow)) is None


def test_matching_job_version_changes_with_structured_vacancy_skills() -> None:
    with _session() as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        cv_file = CvFileRow(
            user_id=user.id,
            original_filename="resume.txt",
            storage_path="resume.txt",
            content_type="text/plain",
            sha256="b" * 64,
            size_bytes=12,
            analyzed_at=datetime.now(UTC),
        )
        vacancy = VacancyRow(
            source_url="https://example.test/versioned-job",
            title="Engineer",
            company="Example",
            description_text="Build services.",
            required_skills=["Python"],
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

        first = MatchingJobService(session).schedule(application.id)
        vacancy.required_skills = ["Python", "Redis"]
        session.commit()
        second = MatchingJobService(session).schedule(application.id)

        assert first.id != second.id
        assert first.task_payload["content_version"] != second.task_payload["content_version"]


def test_force_refresh_keeps_previous_explanation_until_replacement() -> None:
    with _session() as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        cv_file = CvFileRow(
            user_id=user.id,
            original_filename="resume.txt",
            storage_path="resume.txt",
            content_type="text/plain",
            sha256="c" * 64,
            size_bytes=12,
            analyzed_at=datetime.now(UTC),
        )
        vacancy = VacancyRow(
            source_url="https://example.test/refresh-job",
            title="Engineer",
            company="Example",
            description_text="Python is required.",
            required_skills=["Python"],
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
        session.flush()
        session.add(
            ApplicationMatchResultRow(
                application_id=application.id,
                status="scored",
                final_score=77,
                explanation_json={"summary": ["previous explanation"]},
            )
        )
        session.commit()

        MatchingJobService(session).schedule(application.id, force=True)

        aggregate = session.get(ApplicationMatchResultRow, application.id)
        assert aggregate is not None
        assert aggregate.status == "pending"
        assert aggregate.final_score == 77
        assert aggregate.explanation_json == {"summary": ["previous explanation"]}
