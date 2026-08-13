from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.matching.rag_client import RagClient, RagDocumentResult
from app.storage.tables import VacancyRow

logger = structlog.get_logger(__name__)


class VacancyRagIngestionService:
    def __init__(self, session: Session, rag_client: RagClient) -> None:
        self._session = session
        self._rag = rag_client

    async def ingest_vacancy(
        self,
        vacancy_id: str,
        *,
        owner_user_id: str,
        collection: str = "vacancies",
    ) -> RagDocumentResult | None:
        vacancy = self._session.get(VacancyRow, vacancy_id)
        if vacancy is None:
            logger.warning("rag_ingest_vacancy_not_found", vacancy_id=vacancy_id)
            return None
        content = self._build_content(vacancy)
        metadata = {
            "source_type": "vacancy",
            "source_id": vacancy.id,
            "company": vacancy.company,
            "title": vacancy.title,
            "location": vacancy.location,
            "work_format": vacancy.work_format,
            "adapter_name": vacancy.adapter_name,
        }
        try:
            result = await self._rag.ingest_document(
                owner_user_id=owner_user_id,
                external_document_id=f"vacancy:{vacancy.id}",
                content=content,
                collection=collection,
                title=f"{vacancy.title} — {vacancy.company}",
                document_type="text",
                metadata=metadata,
            )
            logger.info(
                "rag_vacancy_ingested",
                vacancy_id=vacancy.id,
                document_id=result.document_id,
                status=result.status,
            )
            return result
        except Exception as error:
            logger.warning(
                "rag_vacancy_ingest_failed",
                vacancy_id=vacancy.id,
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return None

    @staticmethod
    def _build_content(vacancy: VacancyRow) -> str:
        parts: list[str] = []
        if vacancy.title:
            parts.append(f"Title: {vacancy.title}")
        if vacancy.company:
            parts.append(f"Company: {vacancy.company}")
        if vacancy.location:
            parts.append(f"Location: {vacancy.location}")
        if vacancy.work_format and vacancy.work_format != "unspecified":
            parts.append(f"Work format: {vacancy.work_format}")
        if vacancy.salary_text:
            parts.append(f"Salary: {vacancy.salary_text}")
        if vacancy.description_text:
            parts.append(f"\n{vacancy.description_text}")
        return "\n".join(parts)
