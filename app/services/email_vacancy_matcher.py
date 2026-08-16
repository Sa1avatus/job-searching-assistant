"""RAG-backed matching of an employer email to the user's saved vacancies.

Queries the ``vacancies`` collection on the rag-platform and maps each retrieved
``vacancy:{id}`` document back to the user's application, producing a ranked list of
candidate applications for the email-review decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.rag_client import RagClient
from app.matching.rag_collections import VACANCY_COLLECTION
from app.storage.tables import ApplicationRow, VacancyRow

logger = structlog.get_logger(__name__)

_MAX_QUERY_CHARS = 2_000
_DEFAULT_TOP_K = 5


@dataclass(frozen=True, slots=True)
class EmailVacancyCandidate:
    application_id: str
    vacancy_id: str
    company: str
    title: str
    score: float
    rag_rank: int


class EmailVacancyMatcher:
    """Find candidate applications for an email using semantic RAG retrieval."""

    def __init__(self, session: Session, rag_client: RagClient) -> None:
        self._session = session
        self._rag = rag_client

    async def match(
        self,
        user_id: str,
        subject: str,
        body: str,
        *,
        company: str | None = None,
        vacancy_title: str | None = None,
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[EmailVacancyCandidate]:
        query = _build_query(subject, body, company=company, vacancy_title=vacancy_title)
        try:
            response = await self._rag.search(
                query,
                owner_user_id=user_id,
                collections=(VACANCY_COLLECTION,),
                mode="hybrid",
                top_k=top_k,
            )
        except Exception as error:  # noqa: BLE001 - RAG outage must not abort email sync
            logger.warning(
                "email_rag_search_failed",
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return []

        candidates: list[EmailVacancyCandidate] = []
        seen: set[str] = set()
        for result in response.results:
            vacancy_id = _vacancy_id_from_external(result.external_document_id)
            if vacancy_id is None or vacancy_id in seen:
                continue
            seen.add(vacancy_id)
            application = self._session.scalar(
                select(ApplicationRow).where(
                    ApplicationRow.user_id == user_id,
                    ApplicationRow.vacancy_id == vacancy_id,
                )
            )
            if application is None:
                continue
            vacancy = self._session.get(VacancyRow, vacancy_id)
            if vacancy is None:
                continue
            score = result.reranker_score if result.reranker_score is not None else result.score
            candidates.append(
                EmailVacancyCandidate(
                    application_id=application.id,
                    vacancy_id=vacancy_id,
                    company=vacancy.company,
                    title=vacancy.title,
                    score=float(score),
                    rag_rank=result.rank,
                )
            )
        return candidates


def _build_query(
    subject: str,
    body: str,
    *,
    company: str | None,
    vacancy_title: str | None,
) -> str:
    parts: list[str] = []
    if vacancy_title:
        parts.append(vacancy_title)
    if company:
        parts.append(company)
    if subject.strip():
        parts.append(subject.strip())
    if body.strip():
        parts.append(body.strip())
    query = "\n".join(parts)
    return query[:_MAX_QUERY_CHARS]


def _vacancy_id_from_external(external_document_id: str) -> str | None:
    if not external_document_id.startswith("vacancy:"):
        return None
    vacancy_id = external_document_id[len("vacancy:") :].strip()
    return vacancy_id or None
