import asyncio

from app.matching.opensearch_index import SearchHit
from app.matching.rag_client import RagSearchResponse, RagSearchResult
from app.matching.retrieval import HybridRetriever, RagAugmentedRetriever, StoredEvidence
from app.matching.semantic import FakeEmbeddingClient, RetrievalCandidate


class _FakeSearchIndex:
    def __init__(self, *, fail_lexical: bool = False, fail_dense: bool = False) -> None:
        self.fail_lexical = fail_lexical
        self.fail_dense = fail_dense

    async def search_bm25(self, *args: object, **kwargs: object) -> tuple[SearchHit, ...]:
        if self.fail_lexical:
            raise ConnectionError("OpenSearch unavailable")
        return (SearchHit("lexical", 4), SearchHit("both", 2))

    async def search_knn(self, *args: object, **kwargs: object) -> tuple[SearchHit, ...]:
        if self.fail_dense:
            raise ConnectionError("dense unavailable")
        return (SearchHit("both", 10), SearchHit("dense", 5))


class _FakeRepository:
    def __init__(self) -> None:
        self.evidence_text_by_id = {
            "lexical": "Python services",
            "both": "Production Python systems",
            "dense": "Related platform work",
        }

    def load_by_ids(
        self,
        evidence_ids: tuple[str, ...],
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[StoredEvidence, ...]:
        assert user_id == "user-1"
        assert cv_file_id == "cv-1"
        return tuple(
            StoredEvidence(evidence_id, self.evidence_text_by_id[evidence_id])
            for evidence_id in evidence_ids
        )

    def search_lexical(
        self,
        query_text: str,
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[RetrievalCandidate, ...]:
        return (
            RetrievalCandidate(
                "fallback",
                f"Fallback for {query_text}",
                lexical_score=0.5,
                dense_score=None,
                hybrid_score=0.5,
            ),
        )


def test_hybrid_retrieval_fuses_and_orders_bm25_and_dense_results() -> None:
    async def run() -> None:
        retriever = HybridRetriever(
            _FakeSearchIndex(),
            FakeEmbeddingClient(dimensions=4),
            _FakeRepository(),
        )

        candidates = await retriever.retrieve(
            "Python production",
            user_id="user-1",
            cv_file_id="cv-1",
        )

        assert [candidate.evidence_id for candidate in candidates] == [
            "both",
            "lexical",
            "dense",
        ]
        assert candidates[0].lexical_score == 0.5
        assert candidates[0].dense_score == 1

    asyncio.run(run())


def test_hybrid_retrieval_degrades_to_bm25_when_dense_search_fails() -> None:
    async def run() -> None:
        retriever = HybridRetriever(
            _FakeSearchIndex(fail_dense=True),
            FakeEmbeddingClient(dimensions=4),
            _FakeRepository(),
        )

        candidates = await retriever.retrieve(
            "Python",
            user_id="user-1",
            cv_file_id="cv-1",
        )

        assert [candidate.evidence_id for candidate in candidates] == ["lexical", "both"]
        assert all(candidate.dense_score is None for candidate in candidates)

    asyncio.run(run())


def test_hybrid_retrieval_uses_postgres_fallback_when_opensearch_fails() -> None:
    async def run() -> None:
        retriever = HybridRetriever(
            _FakeSearchIndex(fail_lexical=True),
            FakeEmbeddingClient(dimensions=4),
            _FakeRepository(),
        )

        candidates = await retriever.retrieve(
            "Python",
            user_id="user-1",
            cv_file_id="cv-1",
        )

        assert candidates[0].evidence_id == "fallback"
        assert candidates[0].dense_score is None

    asyncio.run(run())


class _FakeRagClient:
    def __init__(self, *, degraded: bool = False) -> None:
        self.degraded = degraded
        self.search_calls: list[dict[str, object]] = []

    async def search(self, query: str, **kwargs) -> RagSearchResponse:
        self.search_calls.append({"query": query, **kwargs})
        return RagSearchResponse(
            request_id="rag-1",
            results=(
                RagSearchResult(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    external_document_id="cv:cv-1",
                    collection="profiles",
                    content="Production Python systems and platform work",
                    score=1.0,
                    reranker_score=0.9,
                    rank=1,
                    metadata={"source_id": "cv-1"},
                ),
            ),
            effective_mode="hybrid",
            degraded=self.degraded,
        )

    async def ingest_document(self, **kwargs):
        raise NotImplementedError

    async def health(self):
        raise NotImplementedError


def test_rag_augmented_retrieval_uses_owner_scoped_profile_search() -> None:
    async def run() -> None:
        local = HybridRetriever(
            _FakeSearchIndex(),
            FakeEmbeddingClient(dimensions=4),
            _FakeRepository(),
        )
        rag = _FakeRagClient()
        retriever = RagAugmentedRetriever(local, rag)

        candidates = await retriever.retrieve(
            "Python production",
            user_id="user-1",
            cv_file_id="cv-1",
        )

        assert rag.search_calls == [
            {
                "query": "Python production",
                "owner_user_id": "user-1",
                "collections": ("profiles",),
                "top_k": 20,
            }
        ]
        assert candidates[0].evidence_id == "both"
        assert candidates[0].hybrid_score > 0.7

    asyncio.run(run())


def test_rag_augmented_retrieval_preserves_local_scores_when_rag_is_degraded() -> None:
    async def run() -> None:
        local = HybridRetriever(
            _FakeSearchIndex(),
            FakeEmbeddingClient(dimensions=4),
            _FakeRepository(),
        )
        baseline = await local.retrieve(
            "Python production",
            user_id="user-1",
            cv_file_id="cv-1",
        )
        retriever = RagAugmentedRetriever(local, _FakeRagClient(degraded=True))

        candidates = await retriever.retrieve(
            "Python production",
            user_id="user-1",
            cv_file_id="cv-1",
        )

        assert candidates == baseline

    asyncio.run(run())
