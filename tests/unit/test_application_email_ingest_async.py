"""Tests for the RAG+LLM email ingestion path and the category→status decision."""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.application_email_events import ApplicationEmailEventService
from app.services.email_classification import EmailClassification
from app.services.email_vacancy_matcher import EmailVacancyCandidate
from app.storage.database import Base
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow, VacancyRow


class _FakeClassifier:
    def __init__(self, classification: EmailClassification) -> None:
        self._classification = classification

    async def classify(self, subject: str, body: str) -> EmailClassification:
        return self._classification


class _FakeMatcher:
    def __init__(self, candidates: list[EmailVacancyCandidate]) -> None:
        self._candidates = candidates

    async def match(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        company: str | None = None,
        vacancy_title: str | None = None,
    ) -> list[EmailVacancyCandidate]:
        return self._candidates


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_vacancy(session: Session, *, application_status: str = "submitted") -> None:
    session.add(UserRow(id="user-1", display_name="Candidate"))
    session.add(
        VacancyRow(
            id="vacancy-1",
            source_url="https://example.test/jobs/1",
            title="Python Engineer",
            company="Example Corp",
        )
    )
    session.add(
        ApplicationRow(
            id="application-1",
            user_id="user-1",
            vacancy_id="vacancy-1",
            status=application_status,
            match_score=80,
        )
    )
    session.commit()


def _candidate() -> list[EmailVacancyCandidate]:
    return [
        EmailVacancyCandidate(
            application_id="application-1",
            vacancy_id="vacancy-1",
            company="Example Corp",
            title="Python Engineer",
            score=0.8,
            rag_rank=1,
        )
    ]


def test_ingest_async_applies_status_on_confident_match() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            service = ApplicationEmailEventService(
                session,
                classifier=_FakeClassifier(
                    EmailClassification(category="application_received", confidence=0.9)
                ),
                matcher=_FakeMatcher(_candidate()),
            )
            result = asyncio.run(
                service.ingest_async("user-1", "Ваша заявка", "Мы получили ваше резюме.")
            )

            assert result.event.category == "application_received"
            assert result.event.application_id == "application-1"
            assert result.event.needs_review is False
            assert result.status_updated is True
            assert session.get(ApplicationRow, "application-1").status == "approved"
    finally:
        engine.dispose()


def test_ingest_async_flags_needs_review_on_low_confidence() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            service = ApplicationEmailEventService(
                session,
                classifier=_FakeClassifier(
                    EmailClassification(category="application_received", confidence=0.3)
                ),
                matcher=_FakeMatcher(_candidate()),
            )
            result = asyncio.run(
                service.ingest_async("user-1", "Ваша заявка", "Мы получили ваше резюме.")
            )

            assert result.event.needs_review is True
            assert result.event.confidence == 0.3
            assert result.status_updated is True
            assert session.get(ApplicationRow, "application-1").status == "needs_review"
    finally:
        engine.dispose()


def test_ingest_async_applies_rejection_on_confident_match() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            service = ApplicationEmailEventService(
                session,
                classifier=_FakeClassifier(
                    EmailClassification(category="rejection", confidence=0.95)
                ),
                matcher=_FakeMatcher(_candidate()),
            )
            result = asyncio.run(
                service.ingest_async("user-1", "Отказ", "К сожалению, мы не готовы продолжить.")
            )

            assert result.event.category == "rejection"
            assert result.event.needs_review is False
            assert session.get(ApplicationRow, "application-1").status == "employer_rejected"
    finally:
        engine.dispose()


def test_ingest_async_ambiguous_match_flags_needs_review_with_candidates() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            candidates = [
                EmailVacancyCandidate(
                    application_id="application-1",
                    vacancy_id="vacancy-1",
                    company="Example Corp",
                    title="Python Engineer",
                    score=0.3,
                    rag_rank=1,
                ),
                EmailVacancyCandidate(
                    application_id="application-1",
                    vacancy_id="vacancy-2",
                    company="Example Corp",
                    title="Data Engineer",
                    score=0.28,
                    rag_rank=2,
                ),
            ]
            service = ApplicationEmailEventService(
                session,
                classifier=_FakeClassifier(
                    EmailClassification(category="rejection", confidence=0.9)
                ),
                matcher=_FakeMatcher(candidates),
            )
            result = asyncio.run(
                service.ingest_async("user-1", "Отказ", "Мы выбрали другого кандидата.")
            )

            assert result.event.needs_review is True
            assert result.event.candidates is not None
            assert len(result.event.candidates) == 2
            assert session.get(ApplicationRow, "application-1").status == "needs_review"
    finally:
        engine.dispose()


def test_ingest_deterministic_maps_application_received_to_approved() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Ваша заявка",
                "Мы получили ваше резюме.",
                company="Example Corp",
                vacancy_title="Python Engineer",
            )

            assert result.event.category == "application_received"
            assert result.event.outcome == "unknown"
            assert result.status_updated is True
            assert session.get(ApplicationRow, "application-1").status == "approved"
    finally:
        engine.dispose()


def test_ingest_deterministic_unmatched_newsletter_is_review() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Newsletter",
                "Read this week's hiring news.",
            )

            assert result.event.needs_review is True
            assert result.event.category == "other"
            assert result.event.application_id is None
    finally:
        engine.dispose()


def test_resolve_link_applies_status_and_clears_review() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            service = ApplicationEmailEventService(
                session,
                classifier=_FakeClassifier(
                    EmailClassification(category="application_received", confidence=0.3)
                ),
                matcher=_FakeMatcher(_candidate()),
            )
            event = asyncio.run(
                service.ingest_async("user-1", "Ваша заявка", "Мы получили ваше резюме.")
            ).event
            assert event.needs_review is True
            assert session.get(ApplicationRow, "application-1").status == "needs_review"

            resolved = service.resolve(
                "user-1", event.id, action="link", application_id="application-1"
            )

            assert resolved.resolved is True
            assert resolved.needs_review is False
            assert resolved.application_id == "application-1"
            assert session.get(ApplicationRow, "application-1").status == "approved"
    finally:
        engine.dispose()


def test_resolve_dismiss_reverts_best_guess_and_unlinks() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            _seed_vacancy(session)
            service = ApplicationEmailEventService(
                session,
                classifier=_FakeClassifier(
                    EmailClassification(category="application_received", confidence=0.3)
                ),
                matcher=_FakeMatcher(_candidate()),
            )
            event = asyncio.run(
                service.ingest_async("user-1", "Ваша заявка", "Мы получили ваше резюме.")
            ).event
            assert session.get(ApplicationRow, "application-1").status == "needs_review"

            resolved = service.resolve("user-1", event.id, action="dismiss")

            assert resolved.resolved is True
            assert resolved.needs_review is False
            assert resolved.application_id is None
            assert session.get(ApplicationRow, "application-1").status == "submitted"
    finally:
        engine.dispose()
