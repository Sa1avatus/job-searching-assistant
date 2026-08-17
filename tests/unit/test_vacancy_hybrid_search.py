"""Tests for vacancy hybrid search: retriever + RRF fusion."""

import asyncio

from app.matching.vacancy_index import VacancySearchHit
from app.matching.vacancy_retriever import VacancyHybridRetriever, _rrf_fuse
from app.matching.semantic import EmbeddingBatch, FakeEmbeddingClient


class _FakeVacancyIndex:
    """Fake OpenSearch vacancy index for testing."""

    def __init__(
        self,
        *,
        bm25_hits: tuple[VacancySearchHit, ...] = (),
        knn_hits: tuple[VacancySearchHit, ...] = (),
        rrf_hits: tuple[VacancySearchHit, ...] = (),
        fail_bm25: bool = False,
        fail_knn: bool = False,
        fail_rrf: bool = False,
    ) -> None:
        self._bm25_hits = bm25_hits
        self._knn_hits = knn_hits
        self._rrf_hits = rrf_hits
        self.fail_bm25 = fail_bm25
        self.fail_knn = fail_knn
        self.fail_rrf = fail_rrf

    async def search_bm25(self, query_text: str, *, limit: int) -> tuple[VacancySearchHit, ...]:
        if self.fail_bm25:
            raise ConnectionError("OpenSearch unavailable")
        return self._bm25_hits[:limit]

    async def search_knn(
        self, vector: tuple[float, ...], *, limit: int
    ) -> tuple[VacancySearchHit, ...]:
        if self.fail_knn:
            raise ConnectionError("knn unavailable")
        return self._knn_hits[:limit]

    async def search_rrf(
        self,
        query_text: str,
        vector: tuple[float, ...],
        *,
        bm25_limit: int,
        knn_limit: int,
        rrf_size: int,
        rrf_k: int = 60,
    ) -> tuple[VacancySearchHit, ...]:
        if self.fail_rrf:
            raise ConnectionError("RRF unavailable")
        return self._rrf_hits[:rrf_size]


def test_rrf_fuse_interleaves_by_rank() -> None:
    bm25 = (
        VacancySearchHit("A", 5.0),
        VacancySearchHit("B", 4.0),
        VacancySearchHit("C", 3.0),
    )
    knn = (
        VacancySearchHit("C", 0.9),
        VacancySearchHit("A", 0.8),
        VacancySearchHit("D", 0.7),
    )
    result = _rrf_fuse(bm25, knn, k=60)
    # A: 1/(60+1) + 1/(60+2) = 0.01639 + 0.01613 = 0.03252
    # C: 1/(60+3) + 1/(60+1) = 0.01587 + 0.01639 = 0.03226
    # B: 1/(60+2) = 0.01613
    # D: 1/(60+3) = 0.01587
    assert result[0] == "A"
    assert result[1] == "C"
    assert result[2] == "B"
    assert result[3] == "D"


def test_rrf_fuse_empty_lists() -> None:
    assert _rrf_fuse((), ()) == []


def test_rrf_fuse_single_list() -> None:
    bm25 = (VacancySearchHit("X", 1.0),)
    result = _rrf_fuse(bm25, ())
    assert result == ["X"]


def test_rrf_fuse_deduplicates() -> None:
    bm25 = (VacancySearchHit("A", 5.0), VacancySearchHit("B", 4.0))
    knn = (VacancySearchHit("B", 0.9), VacancySearchHit("A", 0.8))
    result = _rrf_fuse(bm25, knn, k=60)
    assert len(result) == 2
    assert set(result) == {"A", "B"}


def test_retriever_native_rrf() -> None:
    """When native RRF succeeds, it returns the result directly."""
    expected = (
        VacancySearchHit("v1", 1.0),
        VacancySearchHit("v2", 0.9),
    )
    index = _FakeVacancyIndex(rrf_hits=expected)
    retriever = VacancyHybridRetriever(index, FakeEmbeddingClient(), top_k=100)
    result = asyncio.run(retriever.search("python"))
    assert result == ["v1", "v2"]


def test_retriever_fallback_to_python_rrf() -> None:
    """When native RRF fails, falls back to Python-side RRF."""
    bm25 = (VacancySearchHit("A", 5.0), VacancySearchHit("B", 4.0))
    knn = (VacancySearchHit("B", 0.9), VacancySearchHit("C", 0.8))
    index = _FakeVacancyIndex(bm25_hits=bm25, knn_hits=knn, fail_rrf=True)
    retriever = VacancyHybridRetriever(index, FakeEmbeddingClient(), top_k=100)
    result = asyncio.run(retriever.search("python"))
    # RRF: A and B appear in both, C only in knn
    assert "A" in result
    assert "B" in result
    assert "C" in result


def test_retriever_bm25_only_when_embedding_unavailable() -> None:
    """Without embedding client, uses BM25 only."""
    bm25 = (VacancySearchHit("A", 5.0), VacancySearchHit("B", 4.0))
    index = _FakeVacancyIndex(bm25_hits=bm25, fail_rrf=True)
    retriever = VacancyHybridRetriever(index, None, top_k=100)
    result = asyncio.run(retriever.search("python"))
    assert result == ["A", "B"]


def test_retriever_empty_query() -> None:
    index = _FakeVacancyIndex()
    retriever = VacancyHybridRetriever(index, None, top_k=100)
    result = asyncio.run(retriever.search(""))
    assert result == []


def test_retriever_whitespace_query() -> None:
    index = _FakeVacancyIndex()
    retriever = VacancyHybridRetriever(index, None, top_k=100)
    result = asyncio.run(retriever.search("   "))
    assert result == []


def test_retriever_bm25_error_returns_empty() -> None:
    """When BM25 fails entirely, return empty list."""
    index = _FakeVacancyIndex(fail_bm25=True, fail_rrf=True)
    retriever = VacancyHybridRetriever(index, None, top_k=100)
    result = asyncio.run(retriever.search("python"))
    assert result == []
