import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, document_storage, greenhouse_http_client
from app.storage.database import Base, session_scope
from app.storage.documents import DocumentStorage
from app.storage.tables import WorkflowTaskRow


def test_greenhouse_import_persists_evidence_and_review_boundary() -> None:
    payload_path = Path(__file__).parents[1] / "fixtures" / "greenhouse_job.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def test_http_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[greenhouse_http_client] = test_http_client
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/vacancies/import-greenhouse",
                json={"source_url": "https://boards.greenhouse.io/example/jobs/44444"},
            )
            assert response.status_code == 201
            vacancy = response.json()
            assert vacancy["adapter_name"] == "greenhouse"
            assert vacancy["requires_sensitive_review"] is True
            assert vacancy["source_evidence_url"].endswith("/example/jobs/44444")
            assert any(field["field_id"] == "resume" for field in vacancy["application_fields"])

            user = client.post("/v1/users", json={"display_name": "Candidate"}).json()
            application = client.post(
                "/v1/applications/prepare",
                json={"user_id": user["id"], "vacancy_id": vacancy["id"]},
            ).json()

            assert application["status"] == "awaiting_review"
            assert "Sensitive application fields require human review" in application["warnings"]
            assert "No CV selected" in application["warnings"]
    finally:
        app.dependency_overrides.clear()


def test_greenhouse_application_can_be_routed_to_browser_review(tmp_path: Path) -> None:
    payload_path = Path(__file__).parents[1] / "fixtures" / "greenhouse_job.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def test_http_client() -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[greenhouse_http_client] = test_http_client
    app.dependency_overrides[document_storage] = lambda: DocumentStorage(
        tmp_path / "documents", max_document_bytes=1024
    )
    try:
        with TestClient(app) as client:
            vacancy = client.post(
                "/v1/vacancies/import-greenhouse",
                json={"source_url": "https://boards.greenhouse.io/example/jobs/44444"},
            ).json()
            user = client.post("/v1/users", json={"display_name": "Candidate"}).json()
            cv_file = client.post(
                f"/v1/users/{user['id']}/cv-files",
                files={"file": ("resume.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
            ).json()
            application = client.post(
                "/v1/applications/prepare",
                json={
                    "user_id": user["id"],
                    "vacancy_id": vacancy["id"],
                    "cv_file_id": cv_file["id"],
                },
            ).json()

            missing_answer = client.post(
                f"/v1/applications/{application['id']}/prepare-browser-review",
                json={"confirmation": "prepare_without_submission"},
            )
            materials = client.patch(
                f"/v1/applications/{application['id']}/materials",
                json={
                    "cover_letter_text": "",
                    "screening_answers": [
                        {"field_id": "email", "answer": "candidate@example.test"}
                    ],
                },
            )

            invalid_confirmation = client.post(
                f"/v1/applications/{application['id']}/prepare-browser-review",
                json={"confirmation": "submit"},
            )
            scheduled = client.post(
                f"/v1/applications/{application['id']}/prepare-browser-review",
                json={"confirmation": "prepare_without_submission"},
            )

            assert invalid_confirmation.status_code == 422
            assert missing_answer.status_code == 409
            assert "Required review answers are missing" in missing_answer.json()["detail"]
            assert materials.status_code == 200
            assert scheduled.status_code == 200
            assert scheduled.json()["queue_name"] == "browser"
            assert scheduled.json()["state"] == "scheduled"
            with session_factory() as session:
                task = (
                    session.query(WorkflowTaskRow).filter_by(application_id=application["id"]).one()
                )
                assert task.task_payload == {"workflow": "greenhouse_review"}
                assert task.transitions[-1].evidence[-1] == "submission:false"
    finally:
        app.dependency_overrides.clear()
