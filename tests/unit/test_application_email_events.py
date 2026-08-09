from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.services.application_email_events import ApplicationEmailEventService
from app.services.recruitment import EntityNotFoundError
from app.storage.database import Base
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow, VacancyRow


def test_ingest_classifies_and_persists_only_derived_email_data() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Application update",
                "Unfortunately, we will not be moving forward.",
            )

            assert result.created is True
            assert result.event.outcome == "rejected"
            assert len(result.event.message_fingerprint) == 64
            assert result.event.status_applied is False
            stored = session.scalar(select(ApplicationEmailEventRow))
            assert stored is result.event
    finally:
        engine.dispose()


def test_ingest_returns_existing_event_for_same_message() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            service = ApplicationEmailEventService(session)

            first = service.ingest("user-1", "Next stage", "Please schedule an interview.")
            duplicate = service.ingest(
                "user-1",
                " Next stage ",
                "Please schedule an interview. ",
            )

            assert duplicate.created is False
            assert duplicate.event.id == first.event.id
            assert len(list(session.scalars(select(ApplicationEmailEventRow)))) == 1
    finally:
        engine.dispose()


def test_ingest_requires_existing_user() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            try:
                ApplicationEmailEventService(session).ingest("missing", "Subject", "Body")
            except EntityNotFoundError as error:
                assert str(error) == "User not found"
            else:
                raise AssertionError("Expected EntityNotFoundError")
    finally:
        engine.dispose()


def test_ingest_matches_unique_application_by_exact_normalized_reference() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/1",
                    title="Senior Python Engineer",
                    company="Example Corp",
                )
            )
            session.add(
                ApplicationRow(
                    id="application-1",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    match_score=80,
                )
            )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Next stage",
                "Please schedule an interview.",
                company="  EXAMPLE   CORP ",
                vacancy_title="senior python engineer",
            )

            assert result.event.application_id == "application-1"
    finally:
        engine.dispose()


def test_ingest_does_not_link_an_ambiguous_company_reference() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            for number in (1, 2):
                session.add(
                    VacancyRow(
                        id=f"vacancy-{number}",
                        source_url=f"https://example.test/jobs/{number}",
                        title=f"Engineer {number}",
                        company="Example Corp",
                    )
                )
                session.add(
                    ApplicationRow(
                        id=f"application-{number}",
                        user_id="user-1",
                        vacancy_id=f"vacancy-{number}",
                        match_score=80,
                    )
                )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Application update",
                "Unfortunately, we will not be moving forward.",
                company="Example Corp",
            )

            assert result.event.application_id is None
    finally:
        engine.dispose()
