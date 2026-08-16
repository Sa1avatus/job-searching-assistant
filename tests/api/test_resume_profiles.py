from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, document_storage
from app.config import Settings
from app.storage.database import Base, session_scope
from app.storage.documents import DocumentStorage


def test_multiple_resume_profiles_are_stored_and_selected_independently(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    deleted_resumes: list[tuple[str, str]] = []

    async def delete_resume(_service, owner_user_id: str, cv_file_id: str) -> bool:
        deleted_resumes.append((owner_user_id, cv_file_id))
        return True

    monkeypatch.setattr(
        "app.api.main.get_settings",
        lambda: Settings(
            _env_file=None,
            rag_enabled=True,
            rag_service_url="http://rag.test",
            rag_api_key="test-key",
            rag_project_id="test-project",
        ),
    )
    monkeypatch.setattr("app.api.main.RagSyncService.delete_resume", delete_resume)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[document_storage] = lambda: DocumentStorage(
        tmp_path / "documents", max_document_bytes=8192
    )
    try:
        with TestClient(app) as client:
            user = client.post("/v1/users", json={"display_name": "Multiple CVs"}).json()
            python_cv = client.post(
                f"/v1/users/{user['id']}/cv-files",
                files={"file": ("python.txt", b"Python backend", "text/plain")},
            ).json()
            java_cv = client.post(
                f"/v1/users/{user['id']}/cv-files",
                files={"file": ("java.txt", b"Java backend", "text/plain")},
            ).json()

            not_confirmed = client.post(
                f"/v1/users/{user['id']}/cv-files/{python_cv['id']}/rag-sync"
            )
            assert not_confirmed.status_code == 409

            python_profile = client.put(
                f"/v1/users/{user['id']}/cv-files/{python_cv['id']}/profile",
                json={
                    "skills": ["Python", "FastAPI"],
                    "experience_summary": "Python developer",
                    "search_keywords": "Python FastAPI",
                    "years_of_experience": 5,
                },
            )
            java_profile = client.put(
                f"/v1/users/{user['id']}/cv-files/{java_cv['id']}/profile",
                json={
                    "skills": ["Java", "Spring"],
                    "experience_summary": "Java developer",
                    "search_keywords": "Java Spring",
                    "years_of_experience": 7,
                },
            )
            selected = client.put(
                f"/v1/users/{user['id']}/active-cv-file",
                json={"cv_file_id": python_cv["id"]},
            )
            resumes = client.get(f"/v1/users/{user['id']}/cv-files")
            rag_sync = client.post(f"/v1/users/{user['id']}/cv-files/{python_cv['id']}/rag-sync")

            assert python_profile.status_code == 200
            assert java_profile.status_code == 200
            assert selected.status_code == 200
            assert resumes.status_code == 200
            assert rag_sync.status_code == 200
            assert rag_sync.json()["status"] == "scheduled"
            assert rag_sync.json()["attempt_number"] == 0
            assert rag_sync.json()["failure_code"] is None
            assert rag_sync.json()["task_id"]
            resume_by_name = {resume["original_filename"]: resume for resume in resumes.json()}
            assert resume_by_name["python.txt"]["skills"] == ["Python", "FastAPI"]
            assert resume_by_name["python.txt"]["search_keywords"] == "Python FastAPI"
            assert resume_by_name["python.txt"]["is_active"] is True
            assert resume_by_name["python.txt"]["rag_sync_status"] == "scheduled"
            assert resume_by_name["java.txt"]["skills"] == ["Java", "Spring"]
            assert resume_by_name["java.txt"]["search_keywords"] == "Java Spring"
            assert resume_by_name["java.txt"]["is_active"] is False
            assert resume_by_name["java.txt"]["rag_sync_status"] == "scheduled"

            python_vacancy = client.post(
                "/v1/vacancies",
                json={
                    "source_url": "https://example.test/jobs/python-cv",
                    "title": "Python Engineer",
                    "company": "Example",
                    "required_skills": ["Python"],
                },
            ).json()
            java_vacancy = client.post(
                "/v1/vacancies",
                json={
                    "source_url": "https://example.test/jobs/java-cv",
                    "title": "Python Engineer",
                    "company": "Example",
                    "required_skills": ["Python"],
                },
            ).json()
            python_application = client.post(
                "/v1/applications/prepare",
                json={
                    "user_id": user["id"],
                    "vacancy_id": python_vacancy["id"],
                    "cv_file_id": python_cv["id"],
                },
            ).json()
            java_application = client.post(
                "/v1/applications/prepare",
                json={
                    "user_id": user["id"],
                    "vacancy_id": java_vacancy["id"],
                    "cv_file_id": java_cv["id"],
                },
            ).json()

            assert python_application["match_score"] > java_application["match_score"]
            assert python_application["selected_cv_file_id"] == python_cv["id"]
            assert java_application["selected_cv_file_id"] == java_cv["id"]

            deleted = client.delete(f"/v1/users/{user['id']}/cv-files/{python_cv['id']}")
            remaining = client.get(f"/v1/users/{user['id']}/cv-files").json()
            selected_materials = client.get(
                "/v1/review-queue",
                params={"application_id": python_application["id"]},
            ).json()

            assert deleted.status_code == 204
            assert [item["id"] for item in remaining] == [java_cv["id"]]
            assert remaining[0]["is_active"] is True
            assert selected_materials[0]["selected_cv_filename"] is None
            assert len(list((tmp_path / "documents").glob("*.txt"))) == 1
            assert (
                client.delete(f"/v1/users/{user['id']}/cv-files/{python_cv['id']}").status_code
                == 404
            )
            assert deleted_resumes == [(user["id"], python_cv["id"])]
    finally:
        app.dependency_overrides.clear()
