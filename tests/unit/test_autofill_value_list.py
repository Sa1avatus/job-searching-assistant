from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.security.autofill_encryption import encrypt_autofill_value
from app.services.autofill_value_list import list_autofill_values
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


def test_list_autofill_values_decrypts_sorted_user_values(session: Session) -> None:
    encryption_key = Fernet.generate_key().decode()
    for user_id in ("user-1", "user-2"):
        session.add(UserRow(id=user_id, display_name=user_id))
    session.add_all(
        [
            AutofillValueRow(
                user_id="user-1",
                key="location.city",
                label="City",
                value_type="text",
                encrypted_value=encrypt_autofill_value(
                    "Bangkok", encryption_key=encryption_key
                ),
                is_sensitive=False,
            ),
            AutofillValueRow(
                user_id="user-1",
                key="contact.email",
                label="Email",
                value_type="email",
                encrypted_value=encrypt_autofill_value(
                    "candidate@example.test", encryption_key=encryption_key
                ),
                is_sensitive=True,
            ),
            AutofillValueRow(
                user_id="user-2",
                key="custom.notes",
                label="Notes",
                value_type="text",
                encrypted_value=encrypt_autofill_value(
                    "other", encryption_key=encryption_key
                ),
                is_sensitive=False,
            ),
        ]
    )
    session.commit()

    values = list_autofill_values(
        session, user_id="user-1", encryption_key=encryption_key
    )

    assert [value.key for value in values] == ["contact.email", "location.city"]
    assert [value.serialized_value for value in values] == [
        "candidate@example.test",
        "Bangkok",
    ]
    assert values[0].is_sensitive is True
    assert values[0].requires_review is True
    assert values[0].may_send_to_llm is False
    assert values[1].requires_review is False
    assert values[1].may_send_to_llm is True
    assert not session.new
    assert not session.dirty
    assert not session.deleted


def test_list_autofill_values_rejects_missing_user(session: Session) -> None:
    with pytest.raises(EntityNotFoundError, match="User not found"):
        list_autofill_values(
            session,
            user_id="missing",
            encryption_key=Fernet.generate_key().decode(),
        )
