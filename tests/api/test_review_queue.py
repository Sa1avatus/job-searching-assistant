import struct
import zlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, document_storage, evidence_artifact_storage
from app.domain.models import TaskState
from app.services.recruitment import RecruitmentService
from app.storage.database import Base, session_scope
from app.storage.documents import DocumentStorage
from app.storage.evidence_artifacts import EvidenceArtifactStorage, InvalidEvidenceArtifact
from app.storage.tables import HumanActionCheckpointRow, VacancyRow, WorkflowTaskRow


def _controlled_png() -> bytes:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x20\x70\xc0\xff")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def test_application_moves_through_persistent_review_queue(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    app.dependency_overrides[document_storage] = lambda: DocumentStorage(
        tmp_path / "documents", max_document_bytes=1024
    )
    try:
        with TestClient(app) as client:
            user = client.post("/v1/users", json={"display_name": "Test Candidate"}).json()
            fact_response = client.post(
                f"/v1/users/{user['id']}/facts",
                json={
                    "category": "skill",
                    "name": "Python",
                    "value": "advanced",
                    "is_verified": True,
                },
            )
            email_fact_response = client.post(
                f"/v1/users/{user['id']}/facts",
                json={
                    "category": "contact",
                    "name": "email",
                    "value": "candidate@example.test",
                    "is_verified": True,
                },
            )
            vacancy_response = client.post(
                "/v1/vacancies",
                json={
                    "source_url": "https://example.test/jobs/review-1",
                    "title": "Backend Engineer",
                    "company": "Example",
                    "required_skills": ["Python", "PostgreSQL"],
                },
            )
            vacancy = vacancy_response.json()
            with session_factory() as session:
                vacancy_row = session.get(VacancyRow, vacancy["id"])
                assert vacancy_row is not None
                vacancy_row.application_fields = [
                    {
                        "field_id": "email",
                        "label": "Email",
                        "field_type": "text",
                        "is_required": True,
                        "semantic_category": "email",
                    },
                    {
                        "field_id": "authorization",
                        "label": "Are you authorized to work?",
                        "field_type": "select",
                        "is_required": True,
                        "semantic_category": "work_authorization",
                    },
                    {
                        "field_id": "resume",
                        "label": "Resume",
                        "field_type": "file",
                        "is_required": True,
                        "semantic_category": "resume",
                    },
                ]
                session.commit()
            cv_response = client.post(
                f"/v1/users/{user['id']}/cv-files",
                files={"file": ("resume.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
            )
            cv_file = cv_response.json()
            duplicate_cv_response = client.post(
                f"/v1/users/{user['id']}/cv-files",
                files={"file": ("resume.pdf", b"%PDF-1.7\n%%EOF", "application/pdf")},
            )
            application_response = client.post(
                "/v1/applications/prepare",
                json={
                    "user_id": user["id"],
                    "vacancy_id": vacancy["id"],
                    "cv_file_id": cv_file["id"],
                },
            )
            application = application_response.json()

            assert fact_response.status_code == 201
            assert email_fact_response.status_code == 201
            assert vacancy_response.status_code == 201
            assert cv_response.status_code == 201
            assert duplicate_cv_response.status_code == 201
            assert duplicate_cv_response.json()["id"] == cv_file["id"]
            assert len(list((tmp_path / "documents").glob("*.pdf"))) == 1
            assert application_response.status_code == 201
            assert application["status"] == "awaiting_review"
            assert application["warnings"] == ["Missing required skill: postgresql"]
            assert application["selected_cv_file_id"] == cv_file["id"]

            queue = client.get("/v1/review-queue").json()
            assert len(queue) == 1
            assert queue[0]["company"] == "Example"
            assert queue[0]["adapter_name"] == "generic"
            assert queue[0]["selected_cv_filename"] == "resume.pdf"
            assert queue[0]["current_workflow_state"] == "scheduled"
            assert queue[0]["missing_facts"] == ["Are you authorized to work?"]
            assert queue[0]["requested_legal_declarations"] == ["Are you authorized to work?"]
            assert len(queue[0]["screening_answers"]) == 2
            materials = client.patch(
                f"/v1/applications/{application['id']}/materials",
                json={
                    "cover_letter_text": "  Truthful, user-reviewed letter.  ",
                    "screening_answers": [
                        {"field_id": "email", "answer": "candidate@example.test"},
                        {"field_id": "authorization", "answer": "Yes"},
                    ],
                },
            )
            assert materials.status_code == 200
            assert materials.json()["cover_letter_text"] == "Truthful, user-reviewed letter."
            assert materials.json()["missing_facts"] == []
            assert {
                answer["field_id"]: answer["answer_source"]
                for answer in materials.json()["screening_answers"]
            } == {"email": "profile_fact", "authorization": "human_review"}
            assert (
                client.patch(
                    f"/v1/applications/{application['id']}/materials",
                    json={
                        "cover_letter_text": "Letter",
                        "screening_answers": [{"field_id": "unknown-field", "answer": "value"}],
                    },
                ).status_code
                == 404
            )
            metrics_response = client.get("/metrics")
            assert "application_review_queue_depth 1" in metrics_response.text

            decision = client.post(
                f"/v1/applications/{application['id']}/decision",
                json={"decision": "approve"},
            )
            assert decision.status_code == 200
            assert decision.json()["status"] == "approved"
            task = client.get(f"/v1/applications/{application['id']}/task").json()
            assert task["state"] == "completed"
            assert task["transitions"][-1]["new_state"] == "completed"
            assert task["transitions"][-1]["evidence"][-1] == "decision:approve"
            assert client.get("/v1/review-queue").json() == []
            assert "application_review_queue_depth 0" in client.get("/metrics").text

            deletion = client.delete(f"/v1/users/{user['id']}")
            assert deletion.status_code == 204
            assert list((tmp_path / "documents").glob("*.pdf")) == []
            assert client.delete(f"/v1/users/{user['id']}").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_human_action_checkpoint_is_visible_and_resumable(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    artifact_storage = EvidenceArtifactStorage(tmp_path, max_artifact_bytes=1024)
    app.dependency_overrides[evidence_artifact_storage] = lambda: artifact_storage
    try:
        with TestClient(app) as client:
            user = client.post("/v1/users", json={"display_name": "Human Action"}).json()
            vacancy = client.post(
                "/v1/vacancies",
                json={
                    "source_url": "https://example.test/jobs/human-action-1",
                    "title": "Engineer",
                    "company": "Example",
                    "required_skills": [],
                },
            ).json()
            application = client.post(
                "/v1/applications/prepare",
                json={"user_id": user["id"], "vacancy_id": vacancy["id"]},
            ).json()
            with session_factory() as session:
                task = (
                    session.query(WorkflowTaskRow).filter_by(application_id=application["id"]).one()
                )
                task.state = "running"
                task.attempt_number = 1
                session.commit()
            with session_factory() as session:
                checkpoint = RecruitmentService(session).request_human_action(
                    application["id"],
                    kind="captcha",
                    instructions="Complete the visible CAPTCHA in the authorized browser session.",
                    evidence=("screenshot:controlled-captcha.png",),
                )
                checkpoint_id = checkpoint.id
            screenshot_path = tmp_path / "controlled-captcha.png"
            screenshot_bytes = _controlled_png()
            screenshot_path.write_bytes(screenshot_bytes)
            invalid_screenshot_path = tmp_path / "invalid.png"
            invalid_screenshot_path.write_bytes(b"\x89PNG\r\n\x1a\nnot-an-image")
            with session_factory() as session:
                with pytest.raises(InvalidEvidenceArtifact, match="structure"):
                    artifact_storage.register_screenshot(
                        session,
                        checkpoint_id=checkpoint_id,
                        storage_path=invalid_screenshot_path,
                        content_type="image/png",
                    )
                artifact = artifact_storage.register_screenshot(
                    session,
                    checkpoint_id=checkpoint_id,
                    storage_path=screenshot_path,
                    content_type="image/png",
                )
                artifact_id = artifact.id

            item = client.get("/v1/review-queue").json()[0]
            assert item["active_human_action"] == {
                "id": checkpoint_id,
                "kind": "captcha",
                "status": "waiting",
                "instructions": ("Complete the visible CAPTCHA in the authorized browser session."),
                "evidence": [
                    "screenshot:controlled-captcha.png",
                    f"artifact:{artifact_id}",
                ],
                "artifacts": [
                    {
                        "id": artifact_id,
                        "kind": "screenshot",
                        "content_type": "image/png",
                        "size_bytes": len(screenshot_bytes),
                        "url": f"/v1/evidence/{artifact_id}",
                    }
                ],
            }
            evidence_response = client.get(f"/v1/evidence/{artifact_id}")
            assert evidence_response.status_code == 200
            assert evidence_response.content == screenshot_bytes
            assert evidence_response.headers["content-type"] == "image/png"
            assert evidence_response.headers["cache-control"] == "private, no-store"
            resumed = client.post(
                f"/v1/applications/{application['id']}/resume",
                json={"confirmation": "CAPTCHA completed by the user"},
            )
            assert resumed.status_code == 200
            assert resumed.json()["state"] == "scheduled"
            assert resumed.json()["transitions"][-1]["evidence"] == [
                f"checkpoint:{checkpoint_id}",
                "confirmation:CAPTCHA completed by the user",
            ]
            assert (
                client.post(
                    f"/v1/applications/{application['id']}/resume",
                    json={"confirmation": "duplicate"},
                ).status_code
                == 404
            )
            assert client.delete(f"/v1/users/{user['id']}").status_code == 204
            assert not screenshot_path.exists()
    finally:
        app.dependency_overrides.clear()


def test_failed_review_task_can_be_retried_with_audited_transition(tmp_path) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            user = client.post("/v1/users", json={"display_name": "Retry Candidate"}).json()
            vacancy = client.post(
                "/v1/vacancies",
                json={
                    "source_url": "https://example.test/jobs/retry-1",
                    "title": "Engineer",
                    "company": "Example",
                    "required_skills": [],
                },
            ).json()
            application = client.post(
                "/v1/applications/prepare",
                json={"user_id": user["id"], "vacancy_id": vacancy["id"]},
            ).json()
            with session_factory() as session:
                task = (
                    session.query(WorkflowTaskRow).filter_by(application_id=application["id"]).one()
                )
                task.state = "failed"
                session.commit()

            queue = client.get("/v1/review-queue").json()
            assert queue[0]["current_workflow_state"] == "failed"
            response = client.post(f"/v1/applications/{application['id']}/retry")
            assert response.status_code == 200
            assert response.json()["state"] == "scheduled"
            assert response.json()["transitions"][-1]["evidence"][-1] == "action:retry"
    finally:
        app.dependency_overrides.clear()


def test_application_review_checkpoint_requires_a_decision() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def test_session_scope() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[session_scope] = test_session_scope
    try:
        with TestClient(app) as client:
            user = client.post("/v1/users", json={"display_name": "Reviewer"}).json()
            vacancy = client.post(
                "/v1/vacancies",
                json={
                    "source_url": "https://example.test/jobs/review-decision",
                    "title": "Engineer",
                    "company": "Example",
                    "required_skills": [],
                },
            ).json()
            application = client.post(
                "/v1/applications/prepare",
                json={"user_id": user["id"], "vacancy_id": vacancy["id"]},
            ).json()
            with session_factory() as session:
                task = (
                    session.query(WorkflowTaskRow).filter_by(application_id=application["id"]).one()
                )
                task.state = TaskState.RUNNING.value
                session.commit()
            with session_factory() as session:
                checkpoint = RecruitmentService(session).request_human_action(
                    application["id"],
                    kind="application_review",
                    instructions="Review screenshot evidence.",
                )
                checkpoint_id = checkpoint.id

            resume = client.post(
                f"/v1/applications/{application['id']}/resume",
                json={"confirmation": "reviewed"},
            )
            decision = client.post(
                f"/v1/applications/{application['id']}/decision",
                json={"decision": "approve"},
            )

            assert resume.status_code == 409
            assert "approve, reject, or skip" in resume.json()["detail"]
            assert decision.status_code == 200
            with session_factory() as session:
                checkpoint = session.get(HumanActionCheckpointRow, checkpoint_id)
                assert checkpoint is not None
                assert checkpoint.status == "resolved"
                assert checkpoint.resolution_evidence == ["decision:approve"]
    finally:
        app.dependency_overrides.clear()
