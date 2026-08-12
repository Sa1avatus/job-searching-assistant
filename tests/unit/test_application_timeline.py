from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.application_timeline import ApplicationTimelineService
from app.storage.tables import ApplicationRow, UserRow, VacancyRow


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.storage.database import Base

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _setup(session: Session) -> tuple[str, str]:
    user = UserRow(display_name="Test")
    vacancy = VacancyRow(
        source_url="https://example.test/v1",
        title="Engineer",
        company="Example",
    )
    session.add_all((user, vacancy))
    session.flush()
    application = ApplicationRow(
        user_id=user.id,
        vacancy_id=vacancy.id,
        status="draft",
        match_score=0,
    )
    session.add(application)
    session.commit()
    return application.id, user.id


def test_record_creates_timeline_event() -> None:
    factory = _session_factory()
    with factory() as session:
        application_id, _ = _setup(session)
        service = ApplicationTimelineService(session)

        event = service.record(
            application_id,
            "status_change",
            previous_value="draft",
            new_value="awaiting_review",
        )

        assert event.id is not None
        assert event.application_id == application_id
        assert event.event_type == "status_change"
        assert event.previous_value == "draft"
        assert event.new_value == "awaiting_review"
        assert event.source == "system"


def test_record_status_change_shortcut() -> None:
    factory = _session_factory()
    with factory() as session:
        application_id, _ = _setup(session)
        service = ApplicationTimelineService(session)

        event = service.record_status_change(
            application_id, "draft", "submitted", source="user"
        )

        assert event.event_type == "status_change"
        assert event.previous_value == "draft"
        assert event.new_value == "submitted"
        assert event.source == "user"


def test_list_events_returns_chronological_order() -> None:
    factory = _session_factory()
    with factory() as session:
        application_id, _ = _setup(session)
        service = ApplicationTimelineService(session)

        service.record(application_id, "status_change", new_value="draft")
        service.record(application_id, "status_change", new_value="awaiting_review")
        service.record(application_id, "status_change", new_value="submitted")

        events = service.list_events(application_id)

        assert len(events) == 3
        assert events[0].new_value == "submitted"
        assert events[2].new_value == "draft"


def test_list_events_respects_limit() -> None:
    factory = _session_factory()
    with factory() as session:
        application_id, _ = _setup(session)
        service = ApplicationTimelineService(session)

        for i in range(5):
            service.record(application_id, "note_added", detail={"index": i})

        events = service.list_events(application_id, limit=2)

        assert len(events) == 2
