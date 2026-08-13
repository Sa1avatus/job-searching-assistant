from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.matching.rag_client import RagDocumentResult
from app.services.profile_rag_ingestion import ProfileRagIngestionService
from app.storage.database import Base
from app.storage.tables import CvFileRow, ProfileFactRow, UserRow


@dataclass
class FakeRagClient:
    ingested: list[dict[str, object]] | None = None
    deleted: list[dict[str, object]] | None = None
    should_fail: bool = False

    async def search(self, query, **kwargs):
        raise NotImplementedError

    async def ingest_document(self, **kwargs) -> RagDocumentResult:
        if self.should_fail:
            raise ConnectionError("RAG unavailable")
        if self.ingested is not None:
            self.ingested.append(kwargs)
        return RagDocumentResult(
            document_id="rag-doc-1",
            external_document_id=kwargs.get("external_document_id", ""),
            version=1,
            status="indexed",
            content_hash="abc123",
        )

    async def health(self):
        raise NotImplementedError

    async def delete_document(self, **kwargs) -> bool:
        if self.should_fail:
            raise ConnectionError("RAG unavailable")
        if self.deleted is not None:
            self.deleted.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_ingest_profile_sends_facts_as_content() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Alice")
            session.add(user)
            session.flush()
            session.add_all(
                [
                    ProfileFactRow(
                        user_id=user.id,
                        category="skills",
                        name="python",
                        value="5 years production",
                        is_verified=True,
                    ),
                    ProfileFactRow(
                        user_id=user.id,
                        category="experience",
                        name="seniority",
                        value="senior",
                        is_verified=True,
                    ),
                    ProfileFactRow(
                        user_id=user.id,
                        category="skill",
                        name="unverified-secret-skill",
                        value="must not reach RAG",
                        is_verified=False,
                    ),
                ]
            )
            session.commit()
            rag = FakeRagClient(ingested=[])
            service = ProfileRagIngestionService(session, rag)
            result = await service.ingest_profile(user.id)
            assert result is not None
            assert result.document_id == "rag-doc-1"
            assert len(rag.ingested) == 1
            payload = rag.ingested[0]
            assert payload["owner_user_id"] == user.id
            assert payload["external_document_id"] == f"profile:{user.id}"
            assert payload["collection"] == "profiles"
            assert "python" in str(payload["content"])
            assert "5 years production" in str(payload["content"])
            assert "unverified-secret-skill" not in str(payload["content"])
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ingest_cv_sends_skills_and_summary() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Bob")
            session.add(user)
            session.flush()
            cv = CvFileRow(
                user_id=user.id,
                original_filename="resume.pdf",
                storage_path="resume.pdf",
                content_type="application/pdf",
                sha256="a" * 64,
                size_bytes=1000,
                skills=["Python", "FastAPI"],
                experience_summary="Built production APIs.",
                years_of_experience=5.0,
            )
            session.add(cv)
            session.commit()
            rag = FakeRagClient(ingested=[])
            service = ProfileRagIngestionService(session, rag)
            result = await service.ingest_cv(cv.id)
            assert result is not None
            assert len(rag.ingested) == 1
            payload = rag.ingested[0]
            assert payload["owner_user_id"] == user.id
            assert payload["external_document_id"] == f"cv:{cv.id}"
            assert payload["collection"] == "resumes"
            assert "Built production APIs" in str(payload["content"])
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ingest_profile_returns_none_for_missing_user() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            rag = FakeRagClient(ingested=[])
            service = ProfileRagIngestionService(session, rag)
            result = await service.ingest_profile("nonexistent")
            assert result is None
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ingest_profile_returns_none_on_rag_failure() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Charlie")
            session.add(user)
            session.commit()
            rag = FakeRagClient(should_fail=True)
            service = ProfileRagIngestionService(session, rag)
            result = await service.ingest_profile(user.id)
            assert result is None
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ingest_empty_profile_removes_stale_rag_document() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Empty")
            session.add(user)
            session.commit()
            rag = FakeRagClient(ingested=[], deleted=[])

            result = await ProfileRagIngestionService(session, rag).ingest_profile(user.id)

            assert result is None
            assert rag.ingested == []
            assert rag.deleted == [
                {
                    "owner_user_id": user.id,
                    "external_document_id": f"profile:{user.id}",
                    "collection": "profiles",
                }
            ]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_fallback_skip_is_not_reported_as_indexed() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Fallback")
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
            session.commit()

            class SkippingRagClient(FakeRagClient):
                async def ingest_document(self, **kwargs) -> RagDocumentResult:
                    return RagDocumentResult(
                        document_id="",
                        external_document_id=str(kwargs["external_document_id"]),
                        version=0,
                        status="skipped",
                        content_hash="",
                    )

            result = await ProfileRagIngestionService(
                session,
                SkippingRagClient(),
            ).ingest_profile(user.id)

            assert result is not None
            assert result.status == "skipped"
    finally:
        engine.dispose()
