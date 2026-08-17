"""Index and reindex vacancies into OpenSearch for hybrid search."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.semantic import EmbeddingClient
from app.matching.vacancy_index import VacancyDocument
from app.storage.tables import EmbeddingRecordRow, VacancyRow

logger = structlog.get_logger(__name__)


class VacancyIndexWriter(Protocol):
    def versioned_index_name(self, *, created_at: datetime | None = None) -> str: ...

    async def create_index(self, index_name: str) -> None: ...

    async def switch_aliases(self, index_name: str) -> None: ...

    async def index_documents(
        self,
        documents: tuple[VacancyDocument, ...],
        *,
        target_index: str | None = None,
    ) -> None: ...

    async def delete_index(self, index_name: str) -> None: ...


class VacancyReindexService:
    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        index_writer: VacancyIndexWriter,
        *,
        batch_size: int = 100,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._session = session
        self._embedding_client = embedding_client
        self._index_writer = index_writer
        self._batch_size = batch_size

    async def rebuild(self) -> tuple[str, int]:
        """Full rebuild: create new index, index all vacancies, swap aliases."""
        index_name = self._index_writer.versioned_index_name()
        await self._index_writer.create_index(index_name)
        indexed_count = 0
        try:
            vacancy_rows = tuple(self._session.scalars(select(VacancyRow).order_by(VacancyRow.id)))
            for start in range(0, len(vacancy_rows), self._batch_size):
                batch = vacancy_rows[start : start + self._batch_size]
                documents = await self._build_documents(batch)
                await self._index_writer.index_documents(documents, target_index=index_name)
                indexed_count += len(documents)
            await self._index_writer.switch_aliases(index_name)
            self._session.commit()
            return index_name, indexed_count
        except Exception:
            self._session.rollback()
            await self._best_effort_delete(index_name)
            raise

    async def index_vacancy(self, vacancy_id: str) -> bool:
        """Index a single vacancy by ID. Returns True if indexed, False if not found."""
        vacancy = self._session.get(VacancyRow, vacancy_id)
        if vacancy is None:
            return False
        documents = await self._build_documents((vacancy,))
        if documents:
            await self._index_writer.index_documents(documents)
            self._session.commit()
        return True

    async def delete_vacancy(self, vacancy_id: str) -> None:
        """Remove a vacancy from the index."""
        try:
            await self._index_writer.delete_document(vacancy_id)  # type: ignore[attr-defined]
        except AttributeError:
            logger.warning(
                "vacancy_index_delete_not_supported",
                vacancy_id=vacancy_id,
            )
            return
        except Exception as error:
            logger.warning(
                "vacancy_index_delete_failed",
                vacancy_id=vacancy_id,
                error_type=type(error).__name__,
            )

    async def _build_documents(
        self,
        vacancy_rows: tuple[VacancyRow, ...],
    ) -> tuple[VacancyDocument, ...]:
        if not vacancy_rows:
            return ()
        # Build text for embedding: title + company + location + skills + description
        texts = tuple(_vacancy_embedding_text(row) for row in vacancy_rows)
        embedding_batch = await self._embedding_client.embed(texts)
        indexed_at = datetime.now(UTC)
        documents = []
        for row, vector in zip(vacancy_rows, embedding_batch.vectors, strict=True):
            content_hash = hashlib.sha256(_vacancy_embedding_text(row).encode("utf-8")).hexdigest()
            documents.append(
                VacancyDocument(
                    vacancy_id=row.id,
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    description_text=row.description_text,
                    required_skills=tuple(row.required_skills or ()),
                    preferred_skills=tuple(row.preferred_skills or ()),
                    embedding=vector,
                    embedding_model=embedding_batch.model_name,
                    embedding_revision=embedding_batch.model_revision,
                    content_hash=content_hash,
                    indexed_at=indexed_at,
                )
            )
            self._upsert_embedding_record(
                row,
                model_name=embedding_batch.model_name,
                model_revision=embedding_batch.model_revision,
                dimensions=embedding_batch.dimensions,
                normalization_method=embedding_batch.normalization_method,
                content_hash=content_hash,
                indexed_at=indexed_at,
            )
        return tuple(documents)

    def _upsert_embedding_record(
        self,
        vacancy: VacancyRow,
        *,
        model_name: str,
        model_revision: str,
        dimensions: int,
        normalization_method: str,
        content_hash: str,
        indexed_at: datetime,
    ) -> None:
        existing = self._session.scalar(
            select(EmbeddingRecordRow).where(
                EmbeddingRecordRow.entity_type == "vacancy",
                EmbeddingRecordRow.entity_id == vacancy.id,
                EmbeddingRecordRow.model_name == model_name,
                EmbeddingRecordRow.model_revision == model_revision,
                EmbeddingRecordRow.content_hash == content_hash,
            )
        )
        if existing is None:
            self._session.add(
                EmbeddingRecordRow(
                    entity_type="vacancy",
                    entity_id=vacancy.id,
                    model_name=model_name,
                    model_revision=model_revision,
                    dimensions=dimensions,
                    normalization_method=normalization_method,
                    content_hash=content_hash,
                    index_name="vacancy-write-alias",
                    indexed_at=indexed_at,
                )
            )
            return
        existing.dimensions = dimensions
        existing.normalization_method = normalization_method
        existing.index_name = "vacancy-write-alias"
        existing.indexed_at = indexed_at

    async def _best_effort_delete(self, index_name: str) -> None:
        try:
            await self._index_writer.delete_index(index_name)
        except Exception as cleanup_error:
            logger.warning(
                "failed_to_cleanup_incomplete_vacancy_index",
                index_name=index_name,
                error_type=type(cleanup_error).__name__,
            )


def _vacancy_embedding_text(row: VacancyRow) -> str:
    """Build text for embedding from vacancy fields."""
    parts = [row.title, row.company, row.location]
    if row.required_skills:
        parts.append(" ".join(row.required_skills))
    if row.preferred_skills:
        parts.append(" ".join(row.preferred_skills))
    # Truncate description to avoid exceeding embedding context window
    description = row.description_text[:2000] if row.description_text else ""
    if description:
        parts.append(description)
    return " ".join(parts)
