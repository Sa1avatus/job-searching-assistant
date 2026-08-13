from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.matching.rag_client import RagDocumentResult
from app.services.vacancy_rag_ingestion import VacancyRagIngestionService
from app.storage.database import Base
from app.storage.tables import VacancyRow


@dataclass
class FakeRagClient:
    ingested: list[dict[str, object]] | None = None
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


@pytest.mark.asyncio
async def test_ingest_vacancy_sends_content_and_metadata() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            vacancy = VacancyRow(
                source_url="https://example.test/v1",
                title="Python Engineer",
                company="Example Corp",
                location="Remote",
                work_format="remote",
                salary_text="$100k",
                description_text="Build production services.",
            )
            session.add(vacancy)
            session.commit()
            rag = FakeRagClient(ingested=[])
            service = VacancyRagIngestionService(session, rag)
            result = await service.ingest_vacancy(vacancy.id, owner_user_id="owner-1")
            assert result is not None
            assert result.document_id == "rag-doc-1"
            assert result.status == "indexed"
            assert len(rag.ingested) == 1
            payload = rag.ingested[0]
            assert payload["owner_user_id"] == "owner-1"
            assert payload["external_document_id"] == f"vacancy:{vacancy.id}"
            assert payload["collection"] == "vacancies"
            assert "Python Engineer" in str(payload["content"])
            assert "Example Corp" in str(payload["content"])
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ingest_vacancy_returns_none_for_missing() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            rag = FakeRagClient(ingested=[])
            service = VacancyRagIngestionService(session, rag)
            result = await service.ingest_vacancy("nonexistent", owner_user_id="owner-1")
            assert result is None
            assert rag.ingested == []
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ingest_vacancy_returns_none_on_rag_failure() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            vacancy = VacancyRow(
                source_url="https://example.test/v2",
                title="Engineer",
                company="Example",
            )
            session.add(vacancy)
            session.commit()
            rag = FakeRagClient(should_fail=True)
            service = VacancyRagIngestionService(session, rag)
            result = await service.ingest_vacancy(vacancy.id, owner_user_id="owner-1")
            assert result is None
    finally:
        engine.dispose()
