from collections.abc import Iterator

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as api_main
from app.api.main import app, get_application_email_provider
from app.config import Settings
from app.services.application_email_sync import ApplicationEmailMessage
from app.services.email_integrations import EmailIntegrationService
from app.storage.database import Base, session_scope
from app.storage.tables import UserRow


class FakeEmailProvider:
    async def fetch_messages(self) -> list[ApplicationEmailMessage]:
        return [
            ApplicationEmailMessage(
                subject="Newsletter",
                body="Read this week's hiring news.",
            )
        ]


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_application_email_sync_returns_batch_summary() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        session.add(UserRow(id="user-1", display_name="Candidate"))
        session.commit()

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[get_application_email_provider] = FakeEmailProvider
    try:
        with TestClient(app) as client:
            response = client.post("/v1/users/user-1/application-email-sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "processed": 1,
        "created": 1,
        "duplicates": 0,
        "status_updated": 0,
        "unmatched": 1,
        "unknown": 1,
        "needs_review": 1,
        "failed": 0,
    }


def test_application_email_sync_uses_saved_configuration(monkeypatch) -> None:
    session_factory = _session_factory()
    encryption_key = Fernet.generate_key().decode("ascii")

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        session.add(UserRow(id="user-1", display_name="Candidate"))
        session.commit()
        EmailIntegrationService(session, encryption_key=encryption_key).save(
            user_id="user-1",
            host="imap.example.test",
            port=993,
            username="candidate@example.test",
            password="app-password",
            use_ssl=True,
            mailbox="INBOX",
            enabled=True,
        )

    monkeypatch.setattr(
        api_main,
        "get_settings",
        lambda: Settings(_env_file=None, browser_state_encryption_key=encryption_key),
    )
    monkeypatch.setattr(
        api_main,
        "ImapApplicationEmailProvider",
        lambda integration: FakeEmailProvider(),
    )
    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.post("/v1/users/user-1/application-email-sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["processed"] == 1


def test_application_email_sync_requires_configured_provider(monkeypatch) -> None:
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
            response = client.post("/v1/users/user-1/application-email-sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Email integration is not configured"}


def test_application_email_import_accepts_multiple_eml_files() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        session.add(UserRow(id="user-1", display_name="Candidate"))
        session.commit()

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/users/user-1/application-email-import",
                files=[
                    ("files", ("first.eml", b"Subject: First\n\nBody", "message/rfc822")),
                    ("files", ("second.eml", b"Subject: Second\n\nBody", "message/rfc822")),
                ],
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["processed"] == 2
    assert response.json()["created"] == 2


def test_application_email_import_rejects_unsupported_file_type() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/users/user-1/application-email-import",
                files={"files": ("message.msg", b"data", "application/octet-stream")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Unsupported email file type" in response.json()["detail"]
