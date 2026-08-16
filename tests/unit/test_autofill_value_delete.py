from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.autofill_keys import InvalidAutofillKey
from app.services.autofill_value_delete import delete_autofill_value
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import AutofillValueRow, Base, UserRow


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as value:
        yield value


def _add_user(session: Session, user_id: str) -> None:
    session.add(UserRow(id=user_id, display_name=f"User {user_id}"))
    session.commit()


def _add_value(session: Session, *, user_id: str, key: str = "custom.notes") -> None:
    session.add(
        AutofillValueRow(
            user_id=user_id,
            key=key,
            label="Notes",
            value_type="text",
            encrypted_value=f"ciphertext-{user_id}",
            is_sensitive=False,
        )
    )
    session.commit()


def _value_state(session: Session) -> list[tuple[str, str, str, str]]:
    session.expire_all()
    rows = session.scalars(select(AutofillValueRow)).all()
    return sorted((row.id, row.user_id, row.key, row.encrypted_value) for row in rows)


def _assert_session_clean(session: Session) -> None:
    assert not session.new
    assert not session.dirty
    assert not session.deleted


def test_delete_autofill_value_removes_only_owned_value(session: Session) -> None:
    for user_id in ("user-1", "user-2"):
        _add_user(session, user_id)
        _add_value(session, user_id=user_id)

    result = delete_autofill_value(session, user_id="user-1", key="custom.notes")

    assert result is None
    remaining = session.scalar(select(AutofillValueRow).where(AutofillValueRow.user_id == "user-2"))
    assert remaining is not None
    assert remaining.key == "custom.notes"
    assert (
        session.scalar(select(AutofillValueRow).where(AutofillValueRow.user_id == "user-1")) is None
    )
    _assert_session_clean(session)


@pytest.mark.parametrize(
    ("user_id", "key", "error", "message"),
    [
        ("missing", "custom.notes", EntityNotFoundError, "User not found"),
        ("user-1", "contact.email", EntityNotFoundError, "Autofill value not found"),
        ("user-1", "credentials.password", InvalidAutofillKey, None),
    ],
)
def test_delete_autofill_value_fails_without_changes(
    session: Session,
    user_id: str,
    key: str,
    error: type[Exception],
    message: str | None,
) -> None:
    _add_user(session, "user-1")
    _add_value(session, user_id="user-1")
    before = _value_state(session)

    with pytest.raises(error, match=message):
        delete_autofill_value(session, user_id=user_id, key=key)

    assert _value_state(session) == before
    _assert_session_clean(session)
