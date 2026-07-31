import asyncio

import httpx
import pytest

from app.matching.http_models import (
    HttpEmbeddingClient,
    HttpReranker,
    MatchingModelServiceError,
)
from app.matching.semantic import RetrievalCandidate


def test_http_embedding_client_validates_model_metadata_and_vectors() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "vectors": [[1, 0, 0], [0, 1, 0]],
                    "model_name": "BAAI/bge-m3",
                    "model_revision": "revision-1",
                    "dimensions": 3,
                    "normalization_method": "l2",
                },
            )
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://models.test",
        ) as client:
            embeddings = await HttpEmbeddingClient(client, dimensions=3).embed(("one", "two"))

        assert embeddings.model_revision == "revision-1"
        assert embeddings.vectors == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    asyncio.run(run())


def test_http_embedding_client_rejects_dimension_drift() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "vectors": [[1, 0]],
                    "model_name": "BAAI/bge-m3",
                    "model_revision": "main",
                    "dimensions": 2,
                    "normalization_method": "l2",
                },
            )
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://models.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError, match="expected 3"):
                await HttpEmbeddingClient(client, dimensions=3).embed(("one",))

    asyncio.run(run())


def test_http_reranker_preserves_raw_scores_and_orders_normalized_scores() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "scores": [
                        {"raw_score": -1.2, "normalized_score": 0.2},
                        {"raw_score": 2.4, "normalized_score": 0.9},
                    ],
                    "model_name": "BAAI/bge-reranker-v2-m3",
                    "model_revision": "revision-2",
                },
            )
        )
        candidates = (
            RetrievalCandidate("a", "Evidence A", 0.5, 0.5, 0.5),
            RetrievalCandidate("b", "Evidence B", 0.4, 0.4, 0.4),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://models.test",
        ) as client:
            reranker = HttpReranker(client)
            result = await reranker.rerank("Requirement", candidates)

        assert [item.candidate.evidence_id for item in result] == ["b", "a"]
        assert result[0].raw_score == 2.4
        assert reranker.model_revision == "revision-2"

    asyncio.run(run())


def test_http_reranker_surfaces_provider_failure() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(503, text="loading"))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://models.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError, match="HTTP 503"):
                await HttpReranker(client).rerank(
                    "Requirement",
                    (RetrievalCandidate("a", "Evidence", 1, 1, 1),),
                )

    asyncio.run(run())
