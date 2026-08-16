from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.main as api_main
from app.api.main import app
from app.config import Settings
from app.storage.database import Base, session_scope
from app.storage.tables import ApplicationEmailEventRow, ApplicationRow, UserRow, VacancyRow


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_application_statistics_counts_statuses_for_requested_user() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        user = UserRow(display_name="Candidate")
        other_user = UserRow(display_name="Other candidate")
        session.add_all((user, other_user))
        session.flush()
        for index, status in enumerate(
            (
                "approved",
                "approved",
                "rejected",
                "employer_rejected",
                "submitted",
                "interview",
            )
        ):
            vacancy = VacancyRow(
                source_url=f"https://example.test/jobs/{index}",
                title=f"Role {index}",
                company="Example",
            )
            session.add(vacancy)
            session.flush()
            session.add(
                ApplicationRow(
                    user_id=user.id,
                    vacancy_id=vacancy.id,
                    status=status,
                    match_score=80,
                )
            )
        other_vacancy = VacancyRow(
            source_url="https://example.test/jobs/other",
            title="Other role",
            company="Other",
        )
        session.add(other_vacancy)
        session.flush()
        session.add(
            ApplicationRow(
                user_id=other_user.id,
                vacancy_id=other_vacancy.id,
                status="rejected",
                match_score=40,
            )
        )
        session.add_all(
            (
                ApplicationEmailEventRow(
                    user_id=user.id,
                    message_fingerprint="a" * 64,
                    outcome="rejected",
                ),
                ApplicationEmailEventRow(
                    user_id=user.id,
                    message_fingerprint="b" * 64,
                    outcome="next_stage",
                ),
                ApplicationEmailEventRow(
                    user_id=user.id,
                    message_fingerprint="c" * 64,
                    outcome="unknown",
                ),
                ApplicationEmailEventRow(
                    user_id=other_user.id,
                    message_fingerprint="d" * 64,
                    outcome="rejected",
                ),
            )
        )
        session.commit()
        user_id = user.id

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.get(f"/v1/users/{user_id}/application-statistics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "total": 6,
        "draft": 0,
        "saved": 0,
        "awaiting_review": 0,
        "approved": 2,
        "rejected": 1,
        "employer_rejected": 1,
        "skipped": 0,
        "submitted": 1,
        "interview": 1,
        "needs_review": 0,
        "email_events": 3,
        "email_rejections": 1,
        "email_next_stages": 1,
    }


def test_application_statistics_returns_not_found_for_unknown_user() -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.get("/v1/users/missing/application-statistics")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


def test_application_sync_skips_source_without_saved_session(
    monkeypatch,
    tmp_path,
) -> None:
    session_factory = _session_factory()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    with session_factory() as session:
        user = UserRow(display_name="Candidate")
        vacancy = VacancyRow(
            source_url="https://hh.ru/vacancy/123",
            title="Engineer",
            company="Example",
            adapter_name="headhunter",
        )
        session.add_all((user, vacancy))
        session.flush()
        session.add(
            ApplicationRow(
                user_id=user.id,
                vacancy_id=vacancy.id,
                status="approved",
                match_score=75,
            )
        )
        session.commit()
        user_id = user.id

    settings = Settings(
        _env_file=None,
        artifact_directory=tmp_path,
        browser_state_encryption_key=("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
    )
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    # Mock BrowserWorkerClient.probe to return invalid (no session)

    from app.services.browser_worker_client import BrowserProbeResult

    async def _fake_probe(self, **kw):
        return BrowserProbeResult(valid=False, details="no session")

    monkeypatch.setattr(
        "app.services.browser_worker_client.BrowserWorkerClient.probe",
        _fake_probe,
    )
    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.post(f"/v1/users/{user_id}/application-sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "checked": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 1,
        "failed": 0,
    }
