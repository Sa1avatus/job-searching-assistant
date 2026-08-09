from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, get_application_email_provider
from app.services.application_email_sync import ApplicationEmailMessage
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
        "failed": 0,
    }


def test_application_email_sync_requires_configured_provider() -> None:
    with TestClient(app) as client:
        response = client.post("/v1/users/user-1/application-email-sync")

    assert response.status_code == 503
    assert response.json() == {"detail": "Email integration is not configured"}
