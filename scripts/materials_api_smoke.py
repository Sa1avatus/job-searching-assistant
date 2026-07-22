from __future__ import annotations

import argparse
import struct
import uuid
import zlib

import httpx
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.recruitment import RecruitmentService
from app.storage.evidence_artifacts import EvidenceArtifactStorage
from app.storage.tables import VacancyRow, WorkflowTaskRow


def _controlled_png() -> bytes:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x20\x70\xc0\xff")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test editable review materials")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--database-url",
        default="postgresql+psycopg://recruitment:recruitment@postgres:5432/recruitment",
    )
    parser.add_argument("--api-key", default="")
    parser.add_argument("--keep", action="store_true", help="Keep the review item for UI testing")
    parser.add_argument(
        "--pause-at-human-action",
        action="store_true",
        help="Leave the active checkpoint waiting for UI verification",
    )
    args = parser.parse_args()
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    source_url = f"https://example.test/jobs/materials-smoke-{uuid.uuid4()}"
    vacancy_id: str | None = None

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as client:
        user = client.post("/v1/users", json={"display_name": "Materials Smoke"})
        user.raise_for_status()
        user_id = user.json()["id"]
        try:
            fact = client.post(
                f"/v1/users/{user_id}/facts",
                json={
                    "category": "contact",
                    "name": "email",
                    "value": "candidate@example.test",
                    "is_verified": True,
                },
            )
            fact.raise_for_status()
            vacancy = client.post(
                "/v1/vacancies",
                json={
                    "source_url": source_url,
                    "title": "Materials Engineer",
                    "company": "Example",
                    "required_skills": [],
                },
            )
            vacancy.raise_for_status()
            vacancy_id = vacancy.json()["id"]
            engine = create_engine(args.database_url)
            with Session(engine) as session:
                row = session.get(VacancyRow, vacancy_id)
                if row is None:
                    raise RuntimeError("Vacancy was not persisted")
                row.application_fields = [
                    {
                        "field_id": "email",
                        "label": "Email",
                        "field_type": "text",
                        "is_required": True,
                        "semantic_category": "email",
                    },
                    {
                        "field_id": "authorization",
                        "label": "Work authorization",
                        "field_type": "select",
                        "is_required": True,
                        "semantic_category": "work_authorization",
                    },
                ]
                session.commit()
            application = client.post(
                "/v1/applications/prepare",
                json={"user_id": user_id, "vacancy_id": vacancy_id},
            )
            application.raise_for_status()
            application_id = application.json()["id"]
            materials = client.patch(
                f"/v1/applications/{application_id}/materials",
                json={
                    "cover_letter_text": "User-reviewed smoke-test letter.",
                    "screening_answers": [{"field_id": "authorization", "answer": "Yes"}],
                },
            )
            materials.raise_for_status()
            payload = materials.json()
            sources = {
                answer["field_id"]: answer["answer_source"]
                for answer in payload["screening_answers"]
            }
            if sources != {"email": "profile_fact", "authorization": "human_review"}:
                raise RuntimeError(f"Unexpected answer provenance: {sources}")
            if payload["missing_facts"]:
                raise RuntimeError(f"Unexpected missing facts: {payload['missing_facts']}")
            with Session(engine) as session:
                task = session.scalar(
                    select(WorkflowTaskRow).where(WorkflowTaskRow.application_id == application_id)
                )
                if task is None:
                    raise RuntimeError("Application workflow task was not persisted")
                task.state = "running"
                task.attempt_number = max(task.attempt_number, 1)
                session.commit()
            with Session(engine) as session:
                checkpoint = RecruitmentService(session).request_human_action(
                    application_id,
                    kind="captcha",
                    instructions="Complete controlled human verification.",
                    evidence=("screenshot:controlled-human-action.png",),
                )
                checkpoint_id = checkpoint.id
            settings = get_settings()
            screenshot_directory = settings.artifact_directory / "evidence-smoke"
            screenshot_directory.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshot_directory / f"{uuid.uuid4()}.png"
            screenshot_bytes = _controlled_png()
            screenshot_path.write_bytes(screenshot_bytes)
            artifact_storage = EvidenceArtifactStorage(
                settings.artifact_directory,
                max_artifact_bytes=settings.max_evidence_bytes,
            )
            with Session(engine) as session:
                artifact = artifact_storage.register_screenshot(
                    session,
                    checkpoint_id=checkpoint_id,
                    storage_path=screenshot_path,
                    content_type="image/png",
                )
                artifact_id = artifact.id
            queue = client.get("/v1/review-queue")
            queue.raise_for_status()
            review_item = next(item for item in queue.json() if item["id"] == application_id)
            if review_item["active_human_action"]["id"] != checkpoint_id:
                raise RuntimeError("Active human-action checkpoint was not exposed")
            artifacts = review_item["active_human_action"]["artifacts"]
            if [artifact["id"] for artifact in artifacts] != [artifact_id]:
                raise RuntimeError("Checkpoint screenshot artifact was not exposed")
            evidence_response = client.get(artifacts[0]["url"])
            evidence_response.raise_for_status()
            if evidence_response.content != screenshot_bytes:
                raise RuntimeError("Checkpoint screenshot bytes changed during retrieval")
            print("Materials smoke passed: profile_fact + human_review provenance persisted")
            if args.pause_at_human_action:
                print(f"Human-action checkpoint waiting: checkpoint_id={checkpoint_id}")
            else:
                resumed = client.post(
                    f"/v1/applications/{application_id}/resume",
                    json={"confirmation": "controlled verification completed"},
                )
                resumed.raise_for_status()
                if resumed.json()["state"] != "scheduled":
                    raise RuntimeError("Human-action checkpoint did not resume to scheduled")
                print("Human-action smoke passed: waiting checkpoint resumed to scheduled")
            if args.keep:
                print(
                    f"Kept review fixture: user_id={user_id} vacancy_id={vacancy_id} "
                    f"application_id={application_id}"
                )
        finally:
            if not args.keep:
                client.delete(f"/v1/users/{user_id}")
            if vacancy_id is not None and not args.keep:
                engine = create_engine(args.database_url)
                with Session(engine) as session:
                    session.execute(delete(VacancyRow).where(VacancyRow.id == vacancy_id))
                    session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
