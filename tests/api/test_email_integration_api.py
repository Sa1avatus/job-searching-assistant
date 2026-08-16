from collections.abc import Iterator

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as api_main
from app.api.main import app
from app.config import Settings
from app.storage.database import Base, session_scope
from app.storage.tables import EmailIntegrationRow, UserRow


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_email_integration_api_saves_secret_without_returning_it(monkeypatch) -> None:
    session_factory = _session_factory()
    encryption_key = Fernet.generate_key().decode("ascii")

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        session.add(UserRow(id="user-1", display_name="Candidate"))
        session.commit()

    monkeypatch.setattr(
        api_main,
        "get_settings",
        lambda: Settings(_env_file=None, browser_state_encryption_key=encryption_key),
    )
    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            saved = client.put(
                "/v1/users/user-1/email-integration",
                json={
                    "host": "imap.example.test",
                    "port": 993,
                    "username": "candidate@example.test",
                    "password": "app-password",
                    "use_ssl": True,
                    "mailbox": "INBOX",
                    "enabled": True,
                },
            )
            loaded = client.get("/v1/users/user-1/email-integration")
    finally:
        app.dependency_overrides.clear()

    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert saved.json() == loaded.json()
    assert saved.json()["password_configured"] is True
    assert "password" not in saved.json()
    with session_factory() as session:
        row = session.get(EmailIntegrationRow, "user-1")
        assert row is not None
        assert "app-password" not in row.encrypted_password


def test_email_integration_api_requires_encrypted_storage(monkeypatch) -> None:
    monkeypatch.setattr(
        api_main,
        "get_settings",
        lambda: Settings(_env_file=None, browser_state_encryption_key=None),
    )
    with TestClient(app) as client:
        response = client.put(
            "/v1/users/user-1/email-integration",
            json={
                "host": "imap.example.test",
                "port": 993,
                "username": "candidate@example.test",
                "password": "app-password",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Encrypted storage is not configured"}
