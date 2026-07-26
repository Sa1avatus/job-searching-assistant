from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.opensearch_index import SearchHit
from app.matching.semantic import EmbeddingClient, RetrievalCandidate
from app.observability.metrics import metrics
from app.storage.tables import CandidateEvidenceRow

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredEvidence:
    evidence_id: str
    evidence_text: str


class EvidenceSearchIndex(Protocol):
    async def search_bm25(
        self,
        query_text: str,
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[SearchHit, ...]: ...

    async def search_knn(
        self,
        vector: tuple[float, ...],
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[SearchHit, ...]: ...


class EvidenceRepository(Protocol):
    def load_by_ids(
        self,
        evidence_ids: tuple[str, ...],
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[StoredEvidence, ...]: ...

    def search_lexical(
        self,
        query_text: str,
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]: ...


class SqlEvidenceRepository:
    def __init__(self, session: Session, *, fallback_scan_limit: int = 500) -> None:
        self._session = session
        self._fallback_scan_limit = fallback_scan_limit

    def load_by_ids(
        self,
        evidence_ids: tuple[str, ...],
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[StoredEvidence, ...]:
        if not evidence_ids:
            return ()
        rows = self._session.scalars(
            select(CandidateEvidenceRow).where(
                CandidateEvidenceRow.id.in_(evidence_ids),
                CandidateEvidenceRow.user_id == user_id,
                CandidateEvidenceRow.cv_file_id == cv_file_id,
                CandidateEvidenceRow.is_verified.is_(True),
            )
        )
        evidence_by_id = {
            row.id: StoredEvidence(evidence_id=row.id, evidence_text=row.evidence_text)
            for row in rows
        }
        return tuple(
            evidence_by_id[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        )

    def search_lexical(
        self,
        query_text: str,
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        rows = self._session.scalars(
            select(CandidateEvidenceRow)
            .where(
                CandidateEvidenceRow.user_id == user_id,
                CandidateEvidenceRow.cv_file_id == cv_file_id,
                CandidateEvidenceRow.is_verified.is_(True),
            )
            .limit(self._fallback_scan_limit)
        )
        query_tokens = _tokens(query_text)
        candidates = []
        for row in rows:
            evidence_tokens = _tokens(f"{row.evidence_text} {row.skill_name or ''}")
            lexical_score = (
                len(query_tokens & evidence_tokens) / len(query_tokens) if query_tokens else 0
            )
            if lexical_score:
                candidates.append(
                    RetrievalCandidate(
                        evidence_id=row.id,
                        evidence_text=row.evidence_text,
                        lexical_score=lexical_score,
                        dense_score=None,
                        hybrid_score=lexical_score,
                    )
                )
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (candidate.hybrid_score, candidate.evidence_id),
                reverse=True,
            )[:limit]
        )


class HybridRetriever:
    def __init__(
        self,
        search_index: EvidenceSearchIndex,
        embedding_client: EmbeddingClient,
        evidence_repository: EvidenceRepository,
        *,
        retrieval_limit: int = 20,
    ) -> None:
        if retrieval_limit <= 0:
            raise ValueError("retrieval_limit must be positive")
        self._search_index = search_index
        self._embedding_client = embedding_client
        self._evidence_repository = evidence_repository
        self._retrieval_limit = retrieval_limit

    async def retrieve(
        self,
        requirement_text: str,
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[RetrievalCandidate, ...]:
        try:
            lexical_hits = await self._search_index.search_bm25(
                requirement_text,
                user_id=user_id,
                cv_file_id=cv_file_id,
                limit=self._retrieval_limit,
            )
        except Exception as error:
            metrics.increment("opensearch_errors_total")
            logger.warning(
                "opensearch_lexical_retrieval_failed",
                error_type=type(error).__name__,
                user_id=user_id,
                cv_file_id=cv_file_id,
            )
            return self._evidence_repository.search_lexical(
                requirement_text,
                user_id=user_id,
                cv_file_id=cv_file_id,
                limit=self._retrieval_limit,
            )

        dense_hits: tuple[SearchHit, ...] = ()
        try:
            embedding_batch = await self._embedding_client.embed((requirement_text,))
            dense_hits = await self._search_index.search_knn(
                embedding_batch.vectors[0],
                user_id=user_id,
                cv_file_id=cv_file_id,
                limit=self._retrieval_limit,
            )
        except Exception as error:
            logger.warning(
                "dense_retrieval_failed",
                error_type=type(error).__name__,
                user_id=user_id,
                cv_file_id=cv_file_id,
            )

        candidates = self._fuse(
            lexical_hits,
            dense_hits,
            user_id=user_id,
            cv_file_id=cv_file_id,
        )
        if candidates:
            return candidates
        return self._evidence_repository.search_lexical(
            requirement_text,
            user_id=user_id,
            cv_file_id=cv_file_id,
            limit=self._retrieval_limit,
        )

    def _fuse(
        self,
        lexical_hits: tuple[SearchHit, ...],
        dense_hits: tuple[SearchHit, ...],
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[RetrievalCandidate, ...]:
        lexical_by_id = _normalized_score_by_id(lexical_hits)
        dense_by_id = _normalized_score_by_id(dense_hits)
        evidence_ids = tuple(
            dict.fromkeys(
                [hit.evidence_id for hit in lexical_hits]
                + [hit.evidence_id for hit in dense_hits]
            )
        )
        stored_evidence = self._evidence_repository.load_by_ids(
            evidence_ids,
            user_id=user_id,
            cv_file_id=cv_file_id,
        )
        candidates = [
            RetrievalCandidate(
                evidence_id=evidence.evidence_id,
                evidence_text=evidence.evidence_text,
                lexical_score=lexical_by_id.get(evidence.evidence_id),
                dense_score=dense_by_id.get(evidence.evidence_id),
                hybrid_score=(
                    0.45 * lexical_by_id.get(evidence.evidence_id, 0)
                    + 0.55 * dense_by_id.get(evidence.evidence_id, 0)
                ),
            )
            for evidence in stored_evidence
        ]
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (candidate.hybrid_score, candidate.evidence_id),
                reverse=True,
            )[: self._retrieval_limit]
        )


def _normalized_score_by_id(hits: tuple[SearchHit, ...]) -> dict[str, float]:
    maximum_score = max((hit.score for hit in hits), default=0)
    if maximum_score <= 0:
        return {hit.evidence_id: 0 for hit in hits}
    return {hit.evidence_id: min(1, max(0, hit.score / maximum_score)) for hit in hits}


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[\w#+.-]+", text, flags=re.UNICODE)}
