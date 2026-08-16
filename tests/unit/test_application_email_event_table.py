from datetime import UTC, datetime

import pytest
from sqlalchemy import Boolean, CheckConstraint, DateTime, String, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.storage.database import Base
from app.storage.tables import ApplicationEmailEventRow, UserRow


def test_application_email_event_row_is_privacy_minimal() -> None:
    assert ApplicationEmailEventRow.__tablename__ == "application_email_events"
    columns = set(ApplicationEmailEventRow.__table__.columns.keys())
    assert columns == {
        "id",
        "user_id",
        "application_id",
        "message_fingerprint",
        "outcome",
        "status_applied",
        "processed_at",
    }

    assert isinstance(ApplicationEmailEventRow.__table__.c.id.type, String)
    assert isinstance(ApplicationEmailEventRow.__table__.c.message_fingerprint.type, String)
    assert ApplicationEmailEventRow.__table__.c.message_fingerprint.type.length == 64
    assert isinstance(ApplicationEmailEventRow.__table__.c.status_applied.type, Boolean)
    assert isinstance(ApplicationEmailEventRow.__table__.c.processed_at.type, DateTime)

    application_fk = next(iter(ApplicationEmailEventRow.__table__.c.application_id.foreign_keys))
    assert application_fk.target_fullname == "applications.id"
    assert application_fk.ondelete == "SET NULL"

    unique_constraints = [
        item
        for item in ApplicationEmailEventRow.__table__.constraints
        if isinstance(item, UniqueConstraint)
    ]
    assert any(
        item.name == "uq_application_email_events_user_fingerprint"
        and set(item.columns.keys()) == {"user_id", "message_fingerprint"}
        for item in unique_constraints
    )

    check_constraints = [
        item
        for item in ApplicationEmailEventRow.__table__.constraints
        if isinstance(item, CheckConstraint)
    ]
    assert any(
        item.name == "ck_application_email_events_outcome"
        and "rejected" in item.sqltext.text
        and "next_stage" in item.sqltext.text
        and "unknown" in item.sqltext.text
        for item in check_constraints
    )


def test_application_email_event_fingerprint_is_unique_per_user() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()

            event = ApplicationEmailEventRow(
                user_id="user-1",
                message_fingerprint="a" * 64,
                outcome="rejected",
                processed_at=datetime.now(UTC),
            )
            session.add(event)
            session.commit()

            assert event.status_applied is False

            session.add(
                ApplicationEmailEventRow(
                    user_id="user-1",
                    message_fingerprint="a" * 64,
                    outcome="unknown",
                    processed_at=datetime.now(UTC),
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()
