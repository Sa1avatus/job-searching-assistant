from __future__ import annotations

from collections.abc import Iterator

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.config import get_settings
from app.security.autofill_decryption import decrypt_autofill_value
from app.storage.database import Base, session_scope
from app.storage.tables import AutofillValueRow, UserRow


def test_create_autofill_value_api_encrypts_and_returns_value(monkeypatch) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", encryption_key)
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup_session:
        setup_session.add(UserRow(id="user-1", display_name="Candidate"))
        setup_session.commit()

    def override_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = override_session_scope
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/users/user-1/autofill-values",
                json={
                    "key": "contact.email",
                    "label": "Email",
                    "value_type": "email",
                    "serialized_value": "candidate@example.test",
                    "is_sensitive": True,
                },
            )

        assert response.status_code == 201
        assert response.json()["serialized_value"] == "candidate@example.test"
        assert response.json()["key"] == "contact.email"
        assert response.json()["requires_review"] is True
        assert response.json()["may_send_to_llm"] is False
        with factory() as verification_session:
            row = verification_session.scalar(select(AutofillValueRow))
            assert row is not None
            assert "candidate@example.test" not in row.encrypted_value
            assert (
                decrypt_autofill_value(row.encrypted_value, encryption_key=encryption_key)
                == "candidate@example.test"
            )
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()


def test_create_autofill_value_api_reports_domain_errors(monkeypatch) -> None:
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = override_session_scope
    payload = {
        "key": "credentials.password",
        "label": "Password",
        "value_type": "text",
        "serialized_value": "forbidden",
    }
    try:
        with TestClient(app) as client:
            missing_user = client.post("/v1/users/missing/autofill-values", json=payload)
            with factory() as setup_session:
                setup_session.add(UserRow(id="user-1", display_name="Candidate"))
                setup_session.commit()
            invalid_key = client.post("/v1/users/user-1/autofill-values", json=payload)

        assert missing_user.status_code == 404
        assert invalid_key.status_code == 422
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()


def test_update_autofill_value_api_preserves_metadata(monkeypatch) -> None:
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_BROWSER_STATE_ENCRYPTION_KEY", encryption_key)
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup_session:
        setup_session.add(UserRow(id="user-1", display_name="Candidate"))
        setup_session.add(
            AutofillValueRow(
                id="value-1",
                user_id="user-1",
                key="location.city",
                label="City",
                value_type="text",
                encrypted_value=Fernet(encryption_key.encode()).encrypt(b"Old").decode(),
                is_sensitive=False,
            )
        )
        setup_session.commit()

    def override_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = override_session_scope
    try:
        with TestClient(app) as client:
            response = client.put(
                "/v1/users/user-1/autofill-values/location.city",
                json={"serialized_value": "Bangkok"},
            )

        assert response.status_code == 200
        assert response.json()["serialized_value"] == "Bangkok"
        assert response.json()["id"] == "value-1"
        assert response.json()["label"] == "City"
        with factory() as verification_session:
            row = verification_session.get(AutofillValueRow, "value-1")
            assert row is not None
            assert (
                decrypt_autofill_value(row.encrypted_value, encryption_key=encryption_key)
                == "Bangkok"
            )
    finally:
        app.dependency_overrides.pop(session_scope, None)
        get_settings.cache_clear()


def test_delete_autofill_value_api_is_user_scoped() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup_session:
        setup_session.add_all(
            [
                UserRow(id="user-1", display_name="First"),
                UserRow(id="user-2", display_name="Second"),
            ]
        )
        setup_session.add_all(
            [
                AutofillValueRow(
                    user_id="user-1",
                    key="contact.email",
                    label="Email",
                    value_type="email",
                    encrypted_value="ciphertext-1",
                    is_sensitive=True,
                ),
                AutofillValueRow(
                    user_id="user-2",
                    key="contact.email",
                    label="Email",
                    value_type="email",
                    encrypted_value="ciphertext-2",
                    is_sensitive=True,
                ),
            ]
        )
        setup_session.commit()

    def override_session_scope() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_scope] = override_session_scope
    try:
        with TestClient(app) as client:
            response = client.delete("/v1/users/user-1/autofill-values/contact.email")

        assert response.status_code == 204
        assert response.content == b""
        with factory() as verification_session:
            rows = verification_session.scalars(select(AutofillValueRow)).all()
            assert [(row.user_id, row.key) for row in rows] == [("user-2", "contact.email")]
    finally:
        app.dependency_overrides.pop(session_scope, None)
