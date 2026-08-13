from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.matching.rag_client import RagDocumentResult
from app.services.rag_sync import RagSyncService
from app.storage.database import Base
from app.storage.tables import CvFileRow, ProfileFactRow, UserRow


@dataclass
class FakeRagClient:
    ingested: list[dict[str, object]]

    async def ingest_document(self, **kwargs) -> RagDocumentResult:
        self.ingested.append(kwargs)
        return RagDocumentResult(
            document_id="rag-doc-1",
            external_document_id=str(kwargs["external_document_id"]),
            version=1,
            status="indexed",
            content_hash="abc123",
        )


@pytest.mark.asyncio
async def test_sync_routes_profile_and_resume_to_separate_collections(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Alice")
            session.add(user)
            session.flush()
            session.add(
                ProfileFactRow(
                    user_id=user.id,
                    category="skill",
                    name="Python",
                    value="production",
                    is_verified=True,
                )
            )
            cv = CvFileRow(
                user_id=user.id,
                original_filename="resume.pdf",
                storage_path="resume.pdf",
                content_type="application/pdf",
                sha256="a" * 64,
                size_bytes=1000,
                skills=["Python"],
                experience_summary="Built APIs.",
            )
            session.add(cv)
            session.commit()

            rag = FakeRagClient(ingested=[])
            monkeypatch.setattr("app.services.rag_sync.create_rag_client", lambda **_kwargs: rag)
            settings = Settings(
                rag_enabled=True,
                rag_service_url="http://rag.test",
                rag_api_key=SecretStr("test-key"),
                rag_project_id="project-1",
            )

            service = RagSyncService(session, settings)
            await service.sync_profile(user.id)
            await service.sync_resume(cv.id)

            assert [item["collection"] for item in rag.ingested] == ["profiles", "resumes"]
            assert all(item["owner_user_id"] == user.id for item in rag.ingested)
    finally:
        engine.dispose()
