from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.autofill_keys import InvalidAutofillKey
from app.security.autofill_encryption import (
    InvalidAutofillValueEncryption,
    encrypt_autofill_value,
)
from app.services.autofill_value_read import read_autofill_value
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import AutofillValueRow, Base, UserRow


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


def _add_user(session: Session, user_id: str) -> None:
    session.add(UserRow(id=user_id, display_name=f"User {user_id}"))
    session.commit()


def _add_value(
    session: Session,
    *,
    user_id: str,
    key: str,
    plaintext: str,
    encryption_key: str,
) -> None:
    session.add(
        AutofillValueRow(
            user_id=user_id,
            key=key,
            label="Test value",
            value_type="text",
            encrypted_value=encrypt_autofill_value(plaintext, encryption_key=encryption_key),
            is_sensitive=False,
        )
    )
    session.commit()


def _assert_session_unchanged(session: Session) -> None:
    assert not session.new
    assert not session.dirty
    assert not session.deleted


@pytest.mark.parametrize("plaintext", ["test@example.com", "  Über cool \n string\t"])
def test_read_autofill_value_returns_exact_plaintext(db_session: Session, plaintext: str) -> None:
    encryption_key = Fernet.generate_key().decode()
    _add_user(db_session, "user-1")
    _add_value(
        db_session,
        user_id="user-1",
        key="custom.notes",
        plaintext=plaintext,
        encryption_key=encryption_key,
    )

    result = read_autofill_value(
        db_session,
        user_id="user-1",
        key="custom.notes",
        encryption_key=encryption_key,
    )

    assert result == plaintext
    _assert_session_unchanged(db_session)


def test_read_autofill_value_is_scoped_to_user(db_session: Session) -> None:
    encryption_key = Fernet.generate_key().decode()
    for user_id, plaintext in (("user-1", "first"), ("user-2", "second")):
        _add_user(db_session, user_id)
        _add_value(
            db_session,
            user_id=user_id,
            key="custom.shared_key",
            plaintext=plaintext,
            encryption_key=encryption_key,
        )

    result = read_autofill_value(
        db_session,
        user_id="user-2",
        key="custom.shared_key",
        encryption_key=encryption_key,
    )

    assert result == "second"
    _assert_session_unchanged(db_session)


def test_read_autofill_value_rejects_missing_user(db_session: Session) -> None:
    with pytest.raises(EntityNotFoundError, match="User not found"):
        read_autofill_value(
            db_session,
            user_id="missing",
            key="custom.notes",
            encryption_key=Fernet.generate_key().decode(),
        )
    _assert_session_unchanged(db_session)


def test_read_autofill_value_rejects_missing_value(db_session: Session) -> None:
    _add_user(db_session, "user-1")

    with pytest.raises(EntityNotFoundError, match="Autofill value not found"):
        read_autofill_value(
            db_session,
            user_id="user-1",
            key="custom.notes",
            encryption_key=Fernet.generate_key().decode(),
        )
    _assert_session_unchanged(db_session)


def test_read_autofill_value_propagates_invalid_key(db_session: Session) -> None:
    _add_user(db_session, "user-1")

    with pytest.raises(InvalidAutofillKey):
        read_autofill_value(
            db_session,
            user_id="user-1",
            key="credentials.password",
            encryption_key=Fernet.generate_key().decode(),
        )
    _assert_session_unchanged(db_session)


@pytest.mark.parametrize("token", [None, "corrupt-token"])
def test_read_autofill_value_fails_closed_for_unreadable_ciphertext(
    db_session: Session, token: str | None
) -> None:
    correct_key = Fernet.generate_key().decode()
    _add_user(db_session, "user-1")
    if token is None:
        _add_value(
            db_session,
            user_id="user-1",
            key="custom.secret_note",
            plaintext="secret",
            encryption_key=correct_key,
        )
        encryption_key = Fernet.generate_key().decode()
    else:
        db_session.add(
            AutofillValueRow(
                user_id="user-1",
                key="custom.secret_note",
                label="Secret note",
                value_type="text",
                encrypted_value=token,
                is_sensitive=True,
            )
        )
        db_session.commit()
        encryption_key = correct_key

    with pytest.raises(InvalidAutofillValueEncryption):
        read_autofill_value(
            db_session,
            user_id="user-1",
            key="custom.secret_note",
            encryption_key=encryption_key,
        )
    _assert_session_unchanged(db_session)
