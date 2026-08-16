from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.config import Settings
from app.services.recruitment import RecruitmentService
from app.storage.database import Base, session_scope
from app.storage.tables import ApplicationRow, CvFileRow, UserRow, VacancyRow


def test_delete_user_propagates_owned_rag_document_ids(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_factory() as session:
        user = UserRow(id="user-1", display_name="Candidate")
        cv = CvFileRow(
            id="cv-1",
            user_id=user.id,
            original_filename="resume.pdf",
            storage_path="resume.pdf",
            content_type="application/pdf",
            sha256="a" * 64,
            size_bytes=100,
        )
        vacancy = VacancyRow(
            id="vacancy-1",
            source_url="https://example.test/1",
            title="Engineer",
            company="Example",
        )
        session.add_all((user, cv, vacancy))
        session.flush()
        session.add(
            ApplicationRow(
                user_id=user.id,
                vacancy_id=vacancy.id,
                selected_cv_file_id=cv.id,
                status="draft",
                match_score=50,
            )
        )
        session.commit()

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    captured: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    async def delete_owner_documents(
        _service,
        owner_user_id: str,
        *,
        cv_file_ids: tuple[str, ...],
        vacancy_ids: tuple[str, ...],
    ):
        captured.append((owner_user_id, cv_file_ids, vacancy_ids))
        return None

    monkeypatch.setattr("app.api.main.get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        "app.api.main.RagSyncService.delete_owner_documents",
        delete_owner_documents,
    )
    monkeypatch.setattr(RecruitmentService, "delete_user", lambda *_args: ([], [], []))
    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            response = client.delete("/v1/users/user-1")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 204
    assert captured == [("user-1", ("cv-1",), ("vacancy-1",))]
