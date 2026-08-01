from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.autofill_keys import InvalidAutofillKey
from app.security.autofill_decryption import decrypt_autofill_value
from app.security.autofill_encryption import InvalidAutofillValueEncryption
from app.services.autofill_value_update import update_autofill_value
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


def _setup(session: Session, user_id: str = "user-1") -> AutofillValueRow:
    session.add(UserRow(id=user_id, display_name="Candidate"))
    row = AutofillValueRow(
        user_id=user_id,
        key="custom.notes",
        label="Notes",
        value_type="text",
        encrypted_value="original",
        is_sensitive=False,
    )
    session.add(row)
    session.commit()
    return row


@pytest.mark.parametrize("value", ["ascii", "Юникод", " \n\t "])
def test_update_autofill_value_round_trips_exact_text(session: Session, value: str) -> None:
    row = _setup(session)
    original_metadata = (
        row.id,
        row.user_id,
        row.key,
        row.label,
        row.value_type,
        row.is_sensitive,
        row.created_at.replace(tzinfo=None),
    )
    encryption_key = Fernet.generate_key().decode()

    result = update_autofill_value(
        session,
        user_id="user-1",
        key="custom.notes",
        serialized_value=value,
        encryption_key=encryption_key,
    )

    assert decrypt_autofill_value(result.encrypted_value, encryption_key=encryption_key) == value
    assert (
        result.id,
        result.user_id,
        result.key,
        result.label,
        result.value_type,
        result.is_sensitive,
        result.created_at.replace(tzinfo=None),
    ) == original_metadata


def test_update_autofill_value_is_user_scoped(session: Session) -> None:
    first = _setup(session)
    second = _setup(session, "user-2")
    encryption_key = Fernet.generate_key().decode()

    update_autofill_value(
        session,
        user_id="user-2",
        key="custom.notes",
        serialized_value="changed",
        encryption_key=encryption_key,
    )

    session.refresh(first)
    assert first.encrypted_value == "original"
    assert second.encrypted_value != "original"


@pytest.mark.parametrize(
    ("user_id", "key", "value", "error", "message"),
    [
        ("missing", "custom.notes", "x", EntityNotFoundError, "User not found"),
        (
            "user-1",
            "contact.email",
            "x",
            EntityNotFoundError,
            "Autofill value not found",
        ),
        ("user-1", "credentials.password", "x", InvalidAutofillKey, None),
        (
            "user-1",
            "custom.notes",
            1,
            TypeError,
            "serialized_value must be a string",
        ),
        (
            "user-1",
            "custom.notes",
            "",
            InvalidAutofillValueEncryption,
            "Autofill value cannot be empty",
        ),
        (
            "user-1",
            "custom.notes",
            "x",
            InvalidAutofillValueEncryption,
            None,
        ),
    ],
)
def test_update_autofill_value_fails_closed(
    session: Session,
    user_id: str,
    key: str,
    value: object,
    error: type[Exception],
    message: str | None,
) -> None:
    row = _setup(session)
    original = row.encrypted_value
    encryption_key = (
        "invalid"
        if error is InvalidAutofillValueEncryption and value
        else Fernet.generate_key().decode()
    )

    with pytest.raises(error, match=message):
        update_autofill_value(
            session,
            user_id=user_id,
            key=key,
            serialized_value=value,  # type: ignore[arg-type]
            encryption_key=encryption_key,
        )

    assert row.encrypted_value == original
    assert not session.new
    assert not session.dirty
    assert not session.deleted
