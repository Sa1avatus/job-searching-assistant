import asyncio
import math

import pytest

from app.matching.semantic import (
    EmbeddingBatch,
    FakeEmbeddingClient,
    FakeReranker,
    RetrievalCandidate,
)


def test_fake_embeddings_are_deterministic_and_l2_normalized() -> None:
    async def run() -> None:
        client = FakeEmbeddingClient(dimensions=6)

        first = await client.embed(("Python services", "PostgreSQL"))
        second = await client.embed(("Python services", "PostgreSQL"))

        assert first == second
        assert first.dimensions == 6
        assert first.normalization_method == "l2"
        assert all(
            math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0)
            for vector in first.vectors
        )

    asyncio.run(run())


def test_embedding_batch_rejects_wrong_dimensions() -> None:
    with pytest.raises(ValueError, match="match dimensions"):
        EmbeddingBatch(
            vectors=((1.0, 0.0),),
            model_name="test",
            model_revision="1",
            dimensions=3,
            normalization_method="none",
        )


def test_fake_reranker_orders_candidates_deterministically() -> None:
    async def run() -> None:
        reranker = FakeReranker()
        candidates = (
            RetrievalCandidate("2", "Java services", 0.2, 0.4, 0.3),
            RetrievalCandidate("1", "Python production services", 0.8, 0.9, 0.85),
        )

        result = await reranker.rerank("Python production experience", candidates)

        assert [candidate.candidate.evidence_id for candidate in result] == ["1", "2"]
        assert result[0].normalized_score > result[1].normalized_score

    asyncio.run(run())


def test_retrieval_candidate_rejects_out_of_range_component_score() -> None:
    with pytest.raises(ValueError, match="dense_score"):
        RetrievalCandidate("1", "text", 0.5, 1.1, 0.8)
