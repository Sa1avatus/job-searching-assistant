from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.domain.autofill import AutofillValueType
from app.domain.autofill_keys import InvalidAutofillKey
from app.security.autofill_encryption import InvalidAutofillValueEncryption
from app.services.autofill_values import InvalidAutofillValue, create_autofill_value
from app.services.recruitment import DuplicateEntityError, EntityNotFoundError
from app.storage.database import Base
from app.storage.tables import AutofillValueRow, UserRow


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def _create_user(session: Session, user_id: str) -> None:
    session.add(UserRow(id=user_id, display_name="Candidate"))
    session.commit()


def _encryption_key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_create_autofill_value_persists_only_encrypted_value(session: Session) -> None:
    _create_user(session, "user-1")
    encryption_key = _encryption_key()
    plaintext = "artificial value"

    row = create_autofill_value(
        session,
        user_id="user-1",
        key="contact.email",
        label="  Work   email ",
        value_type=AutofillValueType.TEXT,
        serialized_value=plaintext,
        is_sensitive=True,
        encryption_key=encryption_key,
    )

    assert row.id
    assert row.user_id == "user-1"
    assert row.key == "contact.email"
    assert row.label == "Work email"
    assert row.value_type == "text"
    assert row.is_sensitive is True
    assert row.created_at is not None
    assert row.updated_at is not None
    assert row.encrypted_value != plaintext
    restored = (
        Fernet(encryption_key.encode("ascii"))
        .decrypt(row.encrypted_value.encode("ascii"))
        .decode("utf-8")
    )
    assert restored == plaintext


def test_create_autofill_value_allows_same_key_for_different_users(
    session: Session,
) -> None:
    _create_user(session, "user-1")
    _create_user(session, "user-2")
    encryption_key = _encryption_key()

    for user_id in ("user-1", "user-2"):
        create_autofill_value(
            session,
            user_id=user_id,
            key="custom.shared_key",
            label="Shared",
            value_type=AutofillValueType.TEXT,
            serialized_value=user_id,
            is_sensitive=False,
            encryption_key=encryption_key,
        )

    assert session.scalar(select(func.count()).select_from(AutofillValueRow)) == 2


def test_create_autofill_value_rejects_duplicate_without_disclosure(
    session: Session,
) -> None:
    _create_user(session, "user-1")
    encryption_key = _encryption_key()
    for plaintext in ("first value",):
        create_autofill_value(
            session,
            user_id="user-1",
            key="custom.duplicate_key",
            label="Duplicate",
            value_type=AutofillValueType.TEXT,
            serialized_value=plaintext,
            is_sensitive=False,
            encryption_key=encryption_key,
        )

    with pytest.raises(DuplicateEntityError) as error:
        create_autofill_value(
            session,
            user_id="user-1",
            key="custom.duplicate_key",
            label="Duplicate",
            value_type=AutofillValueType.TEXT,
            serialized_value="private second value",
            is_sensitive=False,
            encryption_key=encryption_key,
        )

    assert str(error.value) == "Autofill key already exists"
    assert "private second value" not in str(error.value)
    assert encryption_key not in str(error.value)
    assert session.scalar(select(func.count()).select_from(AutofillValueRow)) == 1


def test_create_autofill_value_requires_existing_user(session: Session) -> None:
    with pytest.raises(EntityNotFoundError, match="^User not found$"):
        create_autofill_value(
            session,
            user_id="missing",
            key="contact.email",
            label="Email",
            value_type=AutofillValueType.TEXT,
            serialized_value="value",
            is_sensitive=False,
            encryption_key=_encryption_key(),
        )

    assert session.scalar(select(func.count()).select_from(AutofillValueRow)) == 0


@pytest.mark.parametrize("label", ["", "   ", "x" * 201, 123])
def test_create_autofill_value_rejects_invalid_label(
    session: Session,
    label: object,
) -> None:
    _create_user(session, "user-1")

    with pytest.raises(InvalidAutofillValue, match="^Autofill label is invalid$"):
        create_autofill_value(
            session,
            user_id="user-1",
            key="contact.email",
            label=label,  # type: ignore[arg-type]
            value_type=AutofillValueType.TEXT,
            serialized_value="value",
            is_sensitive=False,
            encryption_key=_encryption_key(),
        )


def test_create_autofill_value_rejects_non_enum_type(session: Session) -> None:
    _create_user(session, "user-1")

    with pytest.raises(InvalidAutofillValue, match="^Autofill value type is invalid$"):
        create_autofill_value(
            session,
            user_id="user-1",
            key="contact.email",
            label="Email",
            value_type="text",  # type: ignore[arg-type]
            serialized_value="value",
            is_sensitive=False,
            encryption_key=_encryption_key(),
        )


def test_create_autofill_value_rejects_non_bool_sensitivity(session: Session) -> None:
    _create_user(session, "user-1")

    with pytest.raises(
        InvalidAutofillValue,
        match="^Autofill sensitivity flag is invalid$",
    ):
        create_autofill_value(
            session,
            user_id="user-1",
            key="contact.email",
            label="Email",
            value_type=AutofillValueType.TEXT,
            serialized_value="value",
            is_sensitive=1,  # type: ignore[arg-type]
            encryption_key=_encryption_key(),
        )


def test_create_autofill_value_propagates_key_validation_error(session: Session) -> None:
    _create_user(session, "user-1")

    with pytest.raises(InvalidAutofillKey):
        create_autofill_value(
            session,
            user_id="user-1",
            key="invalid",
            label="Invalid",
            value_type=AutofillValueType.TEXT,
            serialized_value="value",
            is_sensitive=False,
            encryption_key=_encryption_key(),
        )


def test_create_autofill_value_propagates_encryption_error(session: Session) -> None:
    _create_user(session, "user-1")

    with pytest.raises(InvalidAutofillValueEncryption):
        create_autofill_value(
            session,
            user_id="user-1",
            key="contact.email",
            label="Email",
            value_type=AutofillValueType.TEXT,
            serialized_value="private value",
            is_sensitive=False,
            encryption_key="invalid-key",
        )
