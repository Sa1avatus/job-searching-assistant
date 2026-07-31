from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.opensearch_index import EvidenceDocument
from app.matching.semantic import EmbeddingClient
from app.storage.tables import CandidateEvidenceRow, EmbeddingRecordRow

logger = structlog.get_logger(__name__)


class EvidenceIndexWriter(Protocol):
    def versioned_index_name(self, *, created_at: datetime | None = None) -> str: ...

    async def create_index(self, index_name: str) -> None: ...

    async def switch_aliases(self, index_name: str) -> None: ...

    async def index_documents(
        self,
        documents: tuple[EvidenceDocument, ...],
        *,
        target_index: str | None = None,
    ) -> None: ...

    async def delete_index(self, index_name: str) -> None: ...


class EvidenceReindexService:
    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        index_writer: EvidenceIndexWriter,
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
        index_name = self._index_writer.versioned_index_name()
        await self._index_writer.create_index(index_name)
        indexed_count = 0
        try:
            evidence_rows = tuple(
                self._session.scalars(
                    select(CandidateEvidenceRow)
                    .where(CandidateEvidenceRow.is_verified.is_(True))
                    .order_by(CandidateEvidenceRow.id)
                )
            )
            for start in range(0, len(evidence_rows), self._batch_size):
                batch = evidence_rows[start : start + self._batch_size]
                documents = await self._build_documents(batch, index_name=index_name)
                await self._index_writer.index_documents(
                    documents,
                    target_index=index_name,
                )
                indexed_count += len(documents)
            await self._index_writer.switch_aliases(index_name)
            self._session.commit()
            return index_name, indexed_count
        except Exception:
            self._session.rollback()
            await self._best_effort_delete(index_name)
            raise

    async def index_cv(self, *, user_id: str, cv_file_id: str) -> int:
        """Incrementally refresh one CV through the stable write alias."""
        evidence_rows = tuple(
            self._session.scalars(
                select(CandidateEvidenceRow)
                .where(
                    CandidateEvidenceRow.user_id == user_id,
                    CandidateEvidenceRow.cv_file_id == cv_file_id,
                    CandidateEvidenceRow.is_verified.is_(True),
                )
                .order_by(CandidateEvidenceRow.id)
            )
        )
        return await self._index_incremental(evidence_rows)

    async def index_user(self, *, user_id: str) -> int:
        evidence_rows = tuple(
            self._session.scalars(
                select(CandidateEvidenceRow)
                .where(
                    CandidateEvidenceRow.user_id == user_id,
                    CandidateEvidenceRow.is_verified.is_(True),
                )
                .order_by(CandidateEvidenceRow.id)
            )
        )
        return await self._index_incremental(evidence_rows)

    async def _index_incremental(
        self,
        evidence_rows: tuple[CandidateEvidenceRow, ...],
    ) -> int:
        indexed_count = 0
        for start in range(0, len(evidence_rows), self._batch_size):
            batch = evidence_rows[start : start + self._batch_size]
            documents = await self._build_documents(batch, index_name="write-alias")
            await self._index_writer.index_documents(documents)
            indexed_count += len(documents)
        self._session.commit()
        return indexed_count

    async def _build_documents(
        self,
        evidence_rows: tuple[CandidateEvidenceRow, ...],
        *,
        index_name: str,
    ) -> tuple[EvidenceDocument, ...]:
        if not evidence_rows:
            return ()
        embedding_batch = await self._embedding_client.embed(
            tuple(row.normalized_text for row in evidence_rows)
        )
        indexed_at = datetime.now(UTC)
        documents = []
        for row, vector in zip(evidence_rows, embedding_batch.vectors, strict=True):
            content_hash = hashlib.sha256(row.normalized_text.encode("utf-8")).hexdigest()
            documents.append(
                EvidenceDocument(
                    evidence_id=row.id,
                    user_id=row.user_id,
                    cv_file_id=row.cv_file_id,
                    evidence_text=row.evidence_text,
                    normalized_text=row.normalized_text,
                    skill_name=row.skill_name,
                    evidence_type=row.evidence_type,
                    experience_level=row.experience_level,
                    is_verified=row.is_verified,
                    years=row.years,
                    confidence=row.confidence,
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
                index_name=index_name,
                indexed_at=indexed_at,
            )
        return tuple(documents)

    def _upsert_embedding_record(
        self,
        evidence: CandidateEvidenceRow,
        *,
        model_name: str,
        model_revision: str,
        dimensions: int,
        normalization_method: str,
        content_hash: str,
        index_name: str,
        indexed_at: datetime,
    ) -> None:
        existing = self._session.scalar(
            select(EmbeddingRecordRow).where(
                EmbeddingRecordRow.entity_type == "candidate_evidence",
                EmbeddingRecordRow.entity_id == evidence.id,
                EmbeddingRecordRow.model_name == model_name,
                EmbeddingRecordRow.model_revision == model_revision,
                EmbeddingRecordRow.content_hash == content_hash,
            )
        )
        if existing is None:
            self._session.add(
                EmbeddingRecordRow(
                    entity_type="candidate_evidence",
                    entity_id=evidence.id,
                    model_name=model_name,
                    model_revision=model_revision,
                    dimensions=dimensions,
                    normalization_method=normalization_method,
                    content_hash=content_hash,
                    index_name=index_name,
                    indexed_at=indexed_at,
                )
            )
            return
        existing.dimensions = dimensions
        existing.normalization_method = normalization_method
        existing.index_name = index_name
        existing.indexed_at = indexed_at

    async def _best_effort_delete(self, index_name: str) -> None:
        try:
            await self._index_writer.delete_index(index_name)
        except Exception as cleanup_error:
            logger.warning(
                "failed_to_cleanup_incomplete_opensearch_index",
                index_name=index_name,
                error_type=type(cleanup_error).__name__,
            )
