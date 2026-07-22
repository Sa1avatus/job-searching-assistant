from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, document_storage
from app.storage.database import Base, session_scope
from app.storage.documents import DocumentStorage


def test_multiple_resume_profiles_are_stored_and_selected_independently(tmp_path) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[document_storage] = lambda: DocumentStorage(
        tmp_path / "documents", max_document_bytes=4096
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

            assert python_profile.status_code == 200
            assert java_profile.status_code == 200
            assert selected.status_code == 200
            assert resumes.status_code == 200
            resume_by_name = {resume["original_filename"]: resume for resume in resumes.json()}
            assert resume_by_name["python.txt"]["skills"] == ["Python", "FastAPI"]
            assert resume_by_name["python.txt"]["search_keywords"] == "Python FastAPI"
            assert resume_by_name["python.txt"]["is_active"] is True
            assert resume_by_name["java.txt"]["skills"] == ["Java", "Spring"]
            assert resume_by_name["java.txt"]["search_keywords"] == "Java Spring"
            assert resume_by_name["java.txt"]["is_active"] is False

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
    finally:
        app.dependency_overrides.clear()
