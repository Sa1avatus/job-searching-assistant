from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.services.browser_worker_client import BrowserWorkerClient
from app.storage.database import Base, session_scope


class _FakeBrowserWorkerClient:
    async def extract_headhunter(self, *, user_id: str, url: str) -> dict:
        return {
            "source_url": url,
            "title": "Backend Engineer",
            "company": "HH Example",
            "location": "Удалённо",
            "description_text": "Python services",
            "required_skills": ("Python",),
            "form_fields": (),
            "requires_sensitive_review": False,
        }


def test_headhunter_and_linkedin_import_boundaries() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    # Patch BrowserWorkerClient to avoid real HTTP calls
    original_init = BrowserWorkerClient.__init__
    BrowserWorkerClient.__init__ = lambda self, *a, **kw: None
    BrowserWorkerClient.extract_headhunter = _FakeBrowserWorkerClient.extract_headhunter  # type: ignore[assignment]
    try:
        with TestClient(app) as client:
            hh_response = client.post(
                "/v1/vacancies/import-headhunter",
                json={"source_url": "https://hh.ru/vacancy/777"},
            )
            linkedin_response = client.post(
                "/v1/vacancies/import-linkedin-reference",
                json={
                    "source_url": "https://www.linkedin.com/jobs/view/backend-888",
                    "title": "Backend Engineer",
                    "company": "LinkedIn Example",
                    "location": "Remote",
                    "description_text": "Manually supplied by the user",
                },
            )

        assert hh_response.status_code == 201
        assert hh_response.json()["adapter_name"] == "headhunter"
        assert hh_response.json()["required_skills"] == ["Python"]
        assert hh_response.json()["source_evidence_url"] == "https://hh.ru/vacancy/777"
        assert linkedin_response.status_code == 201
        assert linkedin_response.json()["adapter_name"] == "linkedin-reference"
        assert linkedin_response.json()["application_fields"] == []
    finally:
        app.dependency_overrides.clear()
        BrowserWorkerClient.__init__ = original_init  # type: ignore[assignment]
