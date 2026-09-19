"""Email intelligence: classes, validated status updates, explainable linking, retention (4B)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domain.application_email import EmailCategory, EmailClass, email_class_for_category
from app.domain.application_lifecycle import IllegalApplicationTransition, check_transition
from app.services.application_email_events import ApplicationEmailEventService
from app.services.application_lifecycle import SubmissionLedger
from app.storage.database import Base
from app.storage.retention import purge_email_bodies
from app.storage.tables import (
    ApplicationEmailEventRow,
    ApplicationRow,
    ApplicationTimelineEventRow,
    UserRow,
    VacancyRow,
)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(UserRow(id="u1", display_name="C"))
        db.add(UserRow(id="u2", display_name="O"))
        db.commit()
        yield db


def _application(
    db, *, company="Acme Robotics", title="Senior Python Engineer", status="submitted"
):
    vacancy = VacancyRow(
        source_url=f"https://e.test/{company}/{title}",
        title=title,
        company=company,
        adapter_name="headhunter",
    )
    db.add(vacancy)
    db.flush()
    application = ApplicationRow(
        user_id="u1", vacancy_id=vacancy.id, status=status, match_score=1, warnings=[]
    )
    db.add(application)
    db.commit()
    return application.id


REJECTION = ("Application update", "Unfortunately, we will not be moving forward. Acme Robotics")
UNLINKED_REJECTION = ("Application update", "Unfortunately, we will not be moving forward.")
INVITE = (
    "Next steps at Acme Robotics",
    "We would like to invite you to an interview for Senior Python Engineer at Acme Robotics.",
)


# -- classes -----------------------------------------------------------------


def test_every_category_maps_to_one_of_the_five_classes() -> None:
    classes = {email_class_for_category(c.value) for c in EmailCategory}

    assert classes == set(EmailClass)
    assert email_class_for_category("interview_invitation") is EmailClass.INTERVIEW
    assert email_class_for_category("test_assignment") is EmailClass.ACTION_REQUIRED
    assert email_class_for_category("rejection") is EmailClass.REJECTION
    assert email_class_for_category("application_received") is EmailClass.INFORMATIONAL
    assert email_class_for_category(None) is EmailClass.INFORMATIONAL
    assert email_class_for_category("not-a-category") is EmailClass.INFORMATIONAL


# -- validated status updates ------------------------------------------------


def test_only_an_email_may_acknowledge_an_application_automatically() -> None:
    assert check_transition("submitted", "approved", actor="email") is True
    with pytest.raises(IllegalApplicationTransition):
        check_transition("submitted", "approved", actor="system")
    with pytest.raises(IllegalApplicationTransition):
        check_transition("submitted", "approved", actor="human")  # not a human step either
    with pytest.raises(IllegalApplicationTransition):
        check_transition("offer", "interview", actor="email")  # emails cannot move backwards


def test_a_confident_email_updates_the_status_and_audits_it_once(session) -> None:
    application_id = _application(session)
    service = ApplicationEmailEventService(session)

    first = service.ingest("u1", *INVITE)
    again = service.ingest("u1", *INVITE)

    assert first.status_updated is True and again.created is False
    assert session.get(ApplicationRow, application_id).status == "interview"
    changes = session.scalars(
        select(ApplicationTimelineEventRow).where(
            ApplicationTimelineEventRow.event_type == "status_change"
        )
    ).all()
    assert [(c.previous_value, c.new_value, c.source) for c in changes] == [
        ("submitted", "interview", "email_event")
    ]


def test_an_email_that_would_move_an_application_backwards_goes_to_review(session) -> None:
    application_id = _application(session, status="offer")
    service = ApplicationEmailEventService(session)

    result = service.ingest("u1", *INVITE)

    assert result.status_updated is False
    assert session.get(ApplicationRow, application_id).status == "offer"  # untouched
    assert result.event.needs_review is True
    assert (
        "refused" in result.event.review_reason
        and "offer -> interview" in result.event.review_reason
    )


def test_a_rejection_never_reopens_a_withdrawn_application(session) -> None:
    application_id = _application(session, status="withdrawn")

    result = ApplicationEmailEventService(session).ingest("u1", *REJECTION)

    assert session.get(ApplicationRow, application_id).status == "withdrawn"
    assert result.status_updated is False


def test_an_email_outcome_before_the_app_recorded_a_submission_blocks_resubmission(session) -> None:
    application_id = _application(session, status="awaiting_review")

    ApplicationEmailEventService(session).ingest("u1", *INVITE)

    row = SubmissionLedger(session).open_row(application_id)
    assert row is not None and (row.state, row.verified_by) == ("confirmed", "email")


# -- explainable linking -----------------------------------------------------


def test_linking_records_how_it_was_decided(session) -> None:
    _application(session)

    result = ApplicationEmailEventService(session).ingest("u1", *INVITE)

    assert result.event.match_method in {"entity", "text"}
    assert result.event.match_reason
    assert result.event.review_reason is None


def test_an_unlinked_email_says_why_it_needs_a_person(session) -> None:
    result = ApplicationEmailEventService(session).ingest("u1", *UNLINKED_REJECTION)

    assert result.event.needs_review is True
    assert result.event.match_method == "none"
    assert "not linked confidently" in result.event.review_reason


def test_a_low_confidence_email_reports_the_confidence(session) -> None:
    _application(session)

    result = ApplicationEmailEventService(session).ingest(
        "u1", "Hello", "Just checking in about something entirely unrelated."
    )

    assert result.event.needs_review is True
    assert "confidence 0.00" in result.event.review_reason


def test_an_email_never_links_to_another_users_application(session) -> None:
    other = _application(session)
    session.get(ApplicationRow, other).user_id = "u2"
    session.commit()

    result = ApplicationEmailEventService(session).ingest("u1", *INVITE)

    assert result.event.application_id is None and result.status_updated is False
    assert session.get(ApplicationRow, other).status == "submitted"


def test_resolving_a_review_item_records_a_human_change_and_clears_the_reason(session) -> None:
    application_id = _application(session, status="awaiting_review")
    service = ApplicationEmailEventService(session)
    event = service.ingest("u1", *UNLINKED_REJECTION).event
    assert event.needs_review and event.review_reason

    service.resolve("u1", event.id, action="link", application_id=application_id)

    assert session.get(ApplicationRow, application_id).status == "employer_rejected"
    assert event.resolved and event.review_reason is None
    sources = {
        e.source
        for e in session.scalars(
            select(ApplicationTimelineEventRow).where(
                ApplicationTimelineEventRow.event_type == "status_change"
            )
        )
    }
    assert "email_review" in sources


def test_a_human_link_that_breaks_the_lifecycle_is_rejected_not_forced(session) -> None:
    application_id = _application(session, status="withdrawn")
    service = ApplicationEmailEventService(session)
    event = service.ingest("u1", *UNLINKED_REJECTION).event

    with pytest.raises(IllegalApplicationTransition):
        service.resolve("u1", event.id, action="link", application_id=application_id)
    assert session.get(ApplicationRow, application_id).status == "withdrawn"


# -- retention ---------------------------------------------------------------


def _event(db, *, age_days, resolved=False, needs_review=False, body="private text"):
    event = ApplicationEmailEventRow(
        user_id="u1",
        message_fingerprint=f"fp-{age_days}-{resolved}-{needs_review}",
        outcome="unknown",
        subject="s",
        body=body,
        resolved=resolved,
        needs_review=needs_review,
        processed_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(event)
    db.commit()
    return event


def test_old_bodies_are_erased_but_unresolved_review_items_keep_theirs(session) -> None:
    handled = _event(session, age_days=45)
    resolved = _event(session, age_days=45, resolved=True, needs_review=True)
    waiting = _event(session, age_days=45, needs_review=True)
    recent = _event(session, age_days=3)

    report = purge_email_bodies(session, retention_days=30)

    assert report.cleared_bodies == 2
    assert handled.body is None and resolved.body is None
    assert waiting.body == "private text" and recent.body == "private text"
    assert handled.subject == "s" and handled.message_fingerprint  # dedup data survives


def test_retention_rejects_a_non_positive_window(session) -> None:
    with pytest.raises(ValueError):
        purge_email_bodies(session, retention_days=0)
