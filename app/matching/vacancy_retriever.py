"""Hybrid retrieval for vacancy search: BM25 + vector + RRF fusion."""

from __future__ import annotations

import structlog

from app.matching.semantic import EmbeddingClient
from app.matching.vacancy_index import OpenSearchVacancyIndex, VacancySearchHit
from app.observability.metrics import metrics

logger = structlog.get_logger(__name__)


class VacancyHybridRetriever:
    """Search vacancies using BM25 + vector + RRF.

    Primary path: single OpenSearch RRF query (sub_searches).
    Fallback: separate BM25 + knn queries fused in Python with RRF.
    Last resort: BM25-only.
    """

    def __init__(
        self,
        vacancy_index: OpenSearchVacancyIndex,
        embedding_client: EmbeddingClient | None,
        *,
        top_k: int = 100,
        rrf_k: int = 60,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self._vacancy_index = vacancy_index
        self._embedding_client = embedding_client
        self._top_k = top_k
        self._rrf_k = rrf_k

    async def search(self, query_text: str) -> list[str]:
        """Return vacancy IDs ranked by hybrid relevance.

        Returns a list of vacancy_id strings in descending relevance order.
        Empty query returns empty list (caller should use normal ordering).
        """
        normalized = query_text.strip()
        if not normalized:
            return []

        # Try native RRF first (single request)
        try:
            hits = await self._vacancy_index.search_rrf(
                normalized,
                await self._get_embedding(normalized),
                bm25_limit=self._top_k,
                knn_limit=self._top_k,
                rrf_size=self._top_k,
                rrf_k=self._rrf_k,
            )
            if hits:
                metrics.increment("vacancy_search_rrf_total")
                return [hit.vacancy_id for hit in hits]
        except Exception as error:
            logger.debug(
                "vacancy_rrf_native_failed_fallback_python",
                error_type=type(error).__name__,
            )
            metrics.increment("vacancy_search_rrf_fallback_total")

        # Python-side RRF: separate BM25 + knn
        return await self._search_python_rrf(normalized)

    async def _search_python_rrf(self, query_text: str) -> list[str]:
        """Run BM25 and knn separately, fuse with Python RRF."""
        bm25_hits: tuple[VacancySearchHit, ...] = ()
        try:
            bm25_hits = await self._vacancy_index.search_bm25(query_text, limit=self._top_k)
        except Exception as error:
            logger.warning(
                "vacancy_bm25_search_failed",
                error_type=type(error).__name__,
            )
            metrics.increment("vacancy_search_bm25_error_total")
            return []

        knn_hits: tuple[VacancySearchHit, ...] = ()
        embedding = await self._get_embedding(query_text)
        if embedding is not None:
            try:
                knn_hits = await self._vacancy_index.search_knn(embedding, limit=self._top_k)
            except Exception as error:
                logger.warning(
                    "vacancy_knn_search_failed",
                    error_type=type(error).__name__,
                )
                metrics.increment("vacancy_search_knn_error_total")

        if not knn_hits:
            # BM25-only fallback
            metrics.increment("vacancy_search_bm25_only_total")
            return [hit.vacancy_id for hit in bm25_hits]

        # RRF fusion
        fused = _rrf_fuse(bm25_hits, knn_hits, k=self._rrf_k)
        metrics.increment("vacancy_search_python_rrf_total")
        return fused[: self._top_k]

    async def _get_embedding(self, text: str) -> tuple[float, ...] | None:
        """Get embedding for query text. Returns None if embedding unavailable."""
        if self._embedding_client is None:
            return None
        try:
            batch = await self._embedding_client.embed((text,))
            return batch.vectors[0]
        except Exception as error:
            logger.debug(
                "vacancy_query_embedding_failed",
                error_type=type(error).__name__,
            )
            metrics.increment("vacancy_search_embedding_error_total")
            return None


def _rrf_fuse(
    bm25_hits: tuple[VacancySearchHit, ...],
    knn_hits: tuple[VacancySearchHit, ...],
    *,
    k: int = 60,
) -> list[str]:
    """Reciprocal Rank Fusion of two ranked lists.

    RRF score = sum(1 / (k + rank_i)) across all lists where doc appears.
    This avoids scaling issues between BM25 scores and cosine similarity.
    """
    rrf_scores: dict[str, float] = {}

    for rank, hit in enumerate(bm25_hits, start=1):
        rrf_scores[hit.vacancy_id] = rrf_scores.get(hit.vacancy_id, 0.0) + 1.0 / (k + rank)

    for rank, hit in enumerate(knn_hits, start=1):
        rrf_scores[hit.vacancy_id] = rrf_scores.get(hit.vacancy_id, 0.0) + 1.0 / (k + rank)

    return [
        vacancy_id
        for vacancy_id, _ in sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
    ]
