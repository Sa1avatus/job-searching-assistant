import hashlib

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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


def test_ingest_matches_unique_application_from_explicit_email_text() -> None:
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
                "Next stage for Senior Python Engineer",
                "Example Corp would like you to schedule an interview.",
            )

            assert result.event.application_id == "application-1"
            assert result.event.status_applied is True
            assert session.get(ApplicationRow, "application-1").status == "interview"
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
                "Example Corp: unfortunately, we will not be moving forward.",
            )

            assert result.event.application_id is None
            assert result.event.status_applied is False
            assert session.get(ApplicationRow, "application-1").status == "draft"
            assert session.get(ApplicationRow, "application-2").status == "draft"
    finally:
        engine.dispose()


def test_ingest_applies_rejection_to_explicit_application() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/rejected",
                    title="Python Engineer",
                    company="Example Corp",
                )
            )
            session.add(
                ApplicationRow(
                    id="application-1",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    status="submitted",
                    match_score=80,
                )
            )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Application update",
                "Unfortunately, we will not be moving forward.",
                application_id="application-1",
            )

            assert result.event.status_applied is True
            assert session.get(ApplicationRow, "application-1").status == "employer_rejected"
    finally:
        engine.dispose()


def test_ingest_does_not_apply_unknown_outcome() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/unknown",
                    title="Python Engineer",
                    company="Example Corp",
                )
            )
            session.add(
                ApplicationRow(
                    id="application-1",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    status="submitted",
                    match_score=80,
                )
            )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Newsletter",
                "Read this week's hiring news.",
                application_id="application-1",
            )

            assert result.event.outcome == "unknown"
            assert result.event.status_applied is False
            assert session.get(ApplicationRow, "application-1").status == "submitted"
    finally:
        engine.dispose()


def test_ingest_does_not_auto_update_when_disabled() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Test"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/auto",
                    title="Engineer",
                    company="Example",
                )
            )
            session.add(
                ApplicationRow(
                    id="app-auto",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    status="submitted",
                    match_score=80,
                )
            )
            session.commit()

            service = ApplicationEmailEventService(session, auto_update_enabled=False)
            result = service.ingest(
                "user-1",
                "Rejection",
                "Unfortunately, we will not be moving forward.",
                application_id="app-auto",
            )

            assert result.event.outcome == "rejected"
            assert result.event.status_applied is False
            assert session.get(ApplicationRow, "app-auto").status == "submitted"
    finally:
        engine.dispose()


def test_list_review_items_returns_unknown_and_unmatched_events() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            service = ApplicationEmailEventService(session)
            service.ingest(
                "user-1",
                "Application update",
                "Unfortunately, we will not be moving forward.",
            )
            service.ingest("user-1", "Newsletter", "Weekly hiring digest.")
            review_items = service.list_review_items("user-1")
            assert len(review_items) >= 1
            outcomes = {item.outcome for item in review_items}
            assert "unknown" in outcomes
    finally:
        engine.dispose()


def test_ingest_applies_nobleprog_rejection_to_unique_title_with_same_company() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add_all(
                (
                    VacancyRow(
                        id="vacancy-target",
                        source_url="https://example.test/jobs/solutions-architect",
                        title="Solutions Architect & Technical Consultant",
                        company="NobleProg",
                    ),
                    VacancyRow(
                        id="vacancy-other",
                        source_url="https://example.test/jobs/trainer",
                        title="Technical Trainer",
                        company="NobleProg",
                    ),
                )
            )
            session.add_all(
                (
                    ApplicationRow(
                        id="application-target",
                        user_id="user-1",
                        vacancy_id="vacancy-target",
                        status="submitted",
                        match_score=80,
                    ),
                    ApplicationRow(
                        id="application-other",
                        user_id="user-1",
                        vacancy_id="vacancy-other",
                        status="submitted",
                        match_score=75,
                    ),
                )
            )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Solutions Architect & Technical Consultant position",
                (
                    "I regret to inform you that your skillset does not match our "
                    "qualifications for the position. NobleProg"
                ),
            )

            assert result.event.outcome == "rejected"
            assert result.event.application_id == "application-target"
            assert result.status_updated is True
            assert session.get(ApplicationRow, "application-target").status == "employer_rejected"
            assert session.get(ApplicationRow, "application-other").status == "submitted"
    finally:
        engine.dispose()


def test_ingest_matches_sender_company_after_removing_recruiting_suffix() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/engineer",
                    title="Platform Engineer",
                    company="Example Corp",
                )
            )
            session.add(
                ApplicationRow(
                    id="application-1",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    status="submitted",
                    match_score=80,
                )
            )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                "Your application status",
                "We have decided not to proceed with your application.",
                company="Example Corp Recruiting Team",
            )

            assert result.event.outcome == "rejected"
            assert result.event.application_id == "application-1"
            assert result.status_updated is True
            assert session.get(ApplicationRow, "application-1").status == "employer_rejected"
    finally:
        engine.dispose()


def test_ingest_reclassifies_and_applies_previously_unknown_duplicate() -> None:
    engine = create_engine("sqlite:///:memory:")
    subject = "Solutions Architect & Technical Consultant position"
    body = "I regret to inform you that your skillset does not match our qualifications."
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.add(
                VacancyRow(
                    id="vacancy-1",
                    source_url="https://example.test/jobs/solutions-architect",
                    title="Solutions Architect & Technical Consultant",
                    company="NobleProg",
                )
            )
            session.add(
                ApplicationRow(
                    id="application-1",
                    user_id="user-1",
                    vacancy_id="vacancy-1",
                    status="submitted",
                    match_score=80,
                )
            )
            session.add(
                ApplicationEmailEventRow(
                    user_id="user-1",
                    message_fingerprint=hashlib.sha256(f"{subject}\n{body}".encode()).hexdigest(),
                    outcome="unknown",
                )
            )
            session.commit()

            result = ApplicationEmailEventService(session).ingest(
                "user-1",
                subject,
                body,
            )

            assert result.created is False
            assert result.event.outcome == "rejected"
            assert result.event.application_id == "application-1"
            assert result.status_updated is True
            assert session.get(ApplicationRow, "application-1").status == "employer_rejected"
    finally:
        engine.dispose()
