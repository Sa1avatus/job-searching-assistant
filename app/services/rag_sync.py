from __future__ import annotations

from dataclasses import asdict, dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.matching.rag_client import RagDocumentResult, create_rag_client
from app.matching.rag_collections import (
    PROFILE_COLLECTION,
    RESUME_COLLECTION,
    VACANCY_COLLECTION,
)
from app.observability.metrics import metrics
from app.services.profile_rag_ingestion import ProfileRagIngestionService
from app.services.vacancy_rag_ingestion import VacancyRagIngestionService
from app.storage.tables import ApplicationRow, CvFileRow, UserRow, VacancyRow

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RagBackfillResult:
    user_id: str
    profile_indexed: bool
    resumes_attempted: int
    resumes_indexed: int
    resumes_skipped: int
    resumes_failed: int
    vacancies_attempted: int
    vacancies_indexed: int
    vacancies_skipped: int
    vacancies_failed: int
    next_resume_cursor: str | None
    next_vacancy_cursor: str | None
    resumes_has_more: bool
    vacancies_has_more: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RagOwnerDeletionResult:
    attempted: int
    deleted: int
    failed: int


class RagSyncService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._rag = create_rag_client(
            service_url=settings.rag_service_url,
            api_key=(
                settings.rag_api_key.get_secret_value()
                if settings.rag_api_key is not None
                else None
            ),
            project_id=settings.rag_project_id,
            collection=PROFILE_COLLECTION,
            timeout_seconds=settings.rag_timeout_seconds,
            enabled=settings.rag_enabled,
        )
        self._ingestion = ProfileRagIngestionService(session, self._rag)
        self._vacancy_ingestion = VacancyRagIngestionService(session, self._rag)
        self._session = session

    async def sync_profile(self, user_id: str) -> RagDocumentResult | None:
        return await self._ingestion.ingest_profile(user_id)

    async def sync_resume(self, cv_file_id: str) -> RagDocumentResult | None:
        return await self._ingestion.ingest_cv(cv_file_id)

    async def delete_resume(self, owner_user_id: str, cv_file_id: str) -> bool:
        try:
            metrics.increment("rag_delete_attempts_total")
            removed = await self._rag.delete_document(
                owner_user_id=owner_user_id,
                external_document_id=f"cv:{cv_file_id}",
                collection=RESUME_COLLECTION,
            )
            metrics.increment("rag_delete_success_total" if removed else "rag_delete_skipped_total")
            return removed
        except Exception as error:
            metrics.increment("rag_delete_failures_total")
            logger.warning(
                "rag_resume_delete_failed",
                owner_user_id=owner_user_id,
                cv_file_id=cv_file_id,
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return False

    async def delete_owner_documents(
        self,
        owner_user_id: str,
        *,
        cv_file_ids: tuple[str, ...],
        vacancy_ids: tuple[str, ...],
    ) -> RagOwnerDeletionResult:
        documents = [
            (PROFILE_COLLECTION, f"profile:{owner_user_id}"),
            *((RESUME_COLLECTION, f"cv:{cv_file_id}") for cv_file_id in cv_file_ids),
            *((VACANCY_COLLECTION, f"vacancy:{vacancy_id}") for vacancy_id in vacancy_ids),
        ]
        deleted = 0
        failed = 0
        for collection, external_document_id in documents:
            try:
                metrics.increment("rag_delete_attempts_total")
                removed = await self._rag.delete_document(
                    owner_user_id=owner_user_id,
                    external_document_id=external_document_id,
                    collection=collection,
                )
                deleted += int(removed)
                metrics.increment(
                    "rag_delete_success_total" if removed else "rag_delete_skipped_total"
                )
            except Exception as error:
                failed += 1
                metrics.increment("rag_delete_failures_total")
                logger.warning(
                    "rag_owner_document_delete_failed",
                    owner_user_id=owner_user_id,
                    collection=collection,
                    external_document_id=external_document_id,
                    error_type=type(error).__name__,
                )
        logger.info(
            "rag_owner_documents_deleted",
            owner_user_id=owner_user_id,
            attempted=len(documents),
            deleted=deleted,
            failed=failed,
        )
        return RagOwnerDeletionResult(
            attempted=len(documents),
            deleted=deleted,
            failed=failed,
        )

    async def backfill_owner(
        self,
        user_id: str,
        *,
        batch_size: int = 50,
        after_resume_id: str | None = None,
        after_vacancy_id: str | None = None,
    ) -> RagBackfillResult:
        if batch_size < 1 or batch_size > 100:
            raise ValueError("batch_size must be between 1 and 100")
        if self._session.get(UserRow, user_id) is None:
            raise ValueError("user not found")

        resume_query = (
            select(CvFileRow)
            .where(CvFileRow.user_id == user_id)
            .order_by(CvFileRow.id)
            .limit(batch_size + 1)
        )
        if after_resume_id is not None:
            resume_query = resume_query.where(CvFileRow.id > after_resume_id)
        resumes = list(self._session.scalars(resume_query).all())

        vacancy_query = (
            select(VacancyRow)
            .join(ApplicationRow, ApplicationRow.vacancy_id == VacancyRow.id)
            .where(ApplicationRow.user_id == user_id)
            .distinct()
            .order_by(VacancyRow.id)
            .limit(batch_size + 1)
        )
        if after_vacancy_id is not None:
            vacancy_query = vacancy_query.where(VacancyRow.id > after_vacancy_id)
        vacancies = list(self._session.scalars(vacancy_query).all())

        resume_batch = resumes[:batch_size]
        vacancy_batch = vacancies[:batch_size]
        profile_result = await self.sync_profile(user_id)
        resume_results = [await self.sync_resume(cv.id) for cv in resume_batch]
        vacancy_results = [
            await self._vacancy_ingestion.ingest_vacancy(vacancy.id, owner_user_id=user_id)
            for vacancy in vacancy_batch
        ]
        resume_indexed = sum(
            result is not None and result.status != "skipped" for result in resume_results
        )
        resume_skipped = sum(
            result is not None and result.status == "skipped" for result in resume_results
        )
        vacancy_indexed = sum(
            result is not None and result.status != "skipped" for result in vacancy_results
        )
        vacancy_skipped = sum(
            result is not None and result.status == "skipped" for result in vacancy_results
        )
        return RagBackfillResult(
            user_id=user_id,
            profile_indexed=profile_result is not None and profile_result.status != "skipped",
            resumes_attempted=len(resume_batch),
            resumes_indexed=resume_indexed,
            resumes_skipped=resume_skipped,
            resumes_failed=len(resume_batch) - resume_indexed - resume_skipped,
            vacancies_attempted=len(vacancy_batch),
            vacancies_indexed=vacancy_indexed,
            vacancies_skipped=vacancy_skipped,
            vacancies_failed=len(vacancy_batch) - vacancy_indexed - vacancy_skipped,
            next_resume_cursor=(resume_batch[-1].id if resume_batch else after_resume_id),
            next_vacancy_cursor=(vacancy_batch[-1].id if vacancy_batch else after_vacancy_id),
            resumes_has_more=len(resumes) > batch_size,
            vacancies_has_more=len(vacancies) > batch_size,
        )
