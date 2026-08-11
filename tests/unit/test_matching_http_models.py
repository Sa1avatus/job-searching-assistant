import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.matching.http_models import (
    HttpEmbeddingClient,
    HttpReranker,
    MatchingModelServiceError,
)
from app.matching.semantic import RetrievalCandidate


class _ContractDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ContractRerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID = Field(default_factory=uuid4)
    query: str = Field(min_length=1)
    documents: list[_ContractDocument] = Field(min_length=1)
    top_n: int | None = Field(None, ge=1)
    return_documents: bool = True
    truncate: bool = True


class _ContractResult(BaseModel):
    id: str
    score: float
    normalized_score: float | None = Field(default=None, ge=0, le=1)
    rank: int = Field(ge=1)
    text: str | None = None
    metadata: dict[str, Any] | None = None
    token_count: int | None = None
    truncated: bool = False
    cache_hit: bool = False


class _ContractUsage(BaseModel):
    documents_received: int
    documents_scored: int
    cache_hits: int
    latency_ms: int


class _ContractRerankResponse(BaseModel):
    request_id: UUID
    model: str
    model_revision: str
    device: str
    requested_revision: str
    resolved_revision: str
    backend: str
    rerank_mode: str
    active_provider: str
    results: list[_ContractResult]
    usage: _ContractUsage


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
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer test-secret"
            assert request.url.path == "/v1/rerank"
            request_payload = json.loads(request.content)
            _ContractRerankRequest.model_validate(request_payload)
            assert request_payload == {
                "query": "Requirement",
                "documents": [
                    {"id": "a", "text": "Evidence A", "metadata": {}},
                    {"id": "b", "text": "Evidence B", "metadata": {}},
                ],
                "top_n": None,
                "return_documents": False,
                "truncate": True,
            }
            response_payload = {
                "request_id": "123e4567-e89b-12d3-a456-426614174000",
                "model": "BAAI/bge-reranker-v2-m3",
                "model_revision": "revision-2",
                "device": "cpu",
                "requested_revision": "main",
                "resolved_revision": "revision-2",
                "backend": "onnx_pairwise",
                "rerank_mode": "pairwise",
                "active_provider": "CPUExecutionProvider",
                "results": [
                    {"id": "b", "score": 7.0, "normalized_score": 0.9, "rank": 1},
                    {"id": "a", "score": -2.0, "normalized_score": 0.2, "rank": 2},
                ],
                "usage": {
                    "documents_received": 2,
                    "documents_scored": 2,
                    "cache_hits": 0,
                    "latency_ms": 5,
                },
            }
            _ContractRerankResponse.model_validate(response_payload)
            return httpx.Response(200, json=response_payload)

        transport = httpx.MockTransport(handler)
        candidates = (
            RetrievalCandidate("a", "Evidence A", 0.5, 0.5, 0.5),
            RetrievalCandidate("b", "Evidence B", 0.4, 0.4, 0.4),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://models.test",
        ) as client:
            reranker = HttpReranker(client, api_key=SecretStr("test-secret"))
            result = await reranker.rerank("Requirement", candidates)

        assert [item.candidate.evidence_id for item in result] == ["b", "a"]
        assert result[0].raw_score == 7.0
        assert result[0].normalized_score == 0.9
        assert reranker.model_revision == "revision-2"

    asyncio.run(run())


def test_http_reranker_rejects_response_with_unknown_evidence_id() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "request_id": "123e4567-e89b-12d3-a456-426614174000",
                    "model": "test-reranker",
                    "model_revision": "1",
                    "device": "cpu",
                    "results": [{"id": "unknown", "score": 0.5, "rank": 1}],
                    "usage": {
                        "documents_received": 1,
                        "documents_scored": 1,
                        "cache_hits": 0,
                        "latency_ms": 1,
                    },
                },
            )
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://reranker.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError, match="IDs do not match"):
                await HttpReranker(client, api_key=SecretStr("test-secret")).rerank(
                    "Requirement",
                    (RetrievalCandidate("a", "Evidence", 1, 1, 1),),
                )

    asyncio.run(run())


def test_http_reranker_rejects_unbounded_score_without_normalized_score() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "request_id": "123e4567-e89b-12d3-a456-426614174000",
                    "model": "test-reranker",
                    "model_revision": "1",
                    "device": "cpu",
                    "results": [{"id": "a", "score": 7.0, "rank": 1}],
                    "usage": {
                        "documents_received": 1,
                        "documents_scored": 1,
                        "cache_hits": 0,
                        "latency_ms": 1,
                    },
                },
            )
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://reranker.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError) as captured:
                await HttpReranker(client, api_key=SecretStr("test-secret")).rerank(
                    "Requirement",
                    (RetrievalCandidate("a", "Evidence", 1, 1, 1),),
                )

        assert captured.value.code == "contract_mismatch"

    asyncio.run(run())


def test_http_reranker_surfaces_provider_failure() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(503, text="loading"))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://models.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError, match="HTTP 503"):
                await HttpReranker(client, api_key=SecretStr("test-secret")).rerank(
                    "Requirement",
                    (RetrievalCandidate("a", "Evidence", 1, 1, 1),),
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    "status_code,expected_code",
    [
        (401, "authentication_error"),
        (403, "authentication_error"),
        (422, "contract_mismatch"),
        (429, "rate_limited"),
        (503, "service_unavailable"),
    ],
)
def test_http_reranker_normalizes_provider_failures(
    status_code: int,
    expected_code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        secret = "unique-secret-sentinel"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status_code, text=f"private {secret}")
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://reranker.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError) as captured:
                await HttpReranker(client, api_key=SecretStr(secret)).rerank(
                    "Private requirement",
                    (RetrievalCandidate("a", "Private evidence", 1, 1, 1),),
                )

        assert captured.value.code == expected_code
        assert secret not in str(captured.value)
        assert "Private requirement" not in str(captured.value)
        assert "Private evidence" not in str(captured.value)
        assert secret not in caplog.text
        assert "Private requirement" not in caplog.text
        assert "Private evidence" not in caplog.text

    asyncio.run(run())


def test_http_reranker_normalizes_transport_timeout() -> None:
    async def run() -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private timeout detail", request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(timeout),
            base_url="http://reranker.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError) as captured:
                await HttpReranker(client, api_key=SecretStr("secret")).rerank(
                    "Requirement",
                    (RetrievalCandidate("a", "Evidence", 1, 1, 1),),
                )

        assert captured.value.code == "timeout"
        assert "private timeout detail" not in str(captured.value)

    asyncio.run(run())


def test_http_reranker_normalizes_transport_error() -> None:
    async def run() -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("private connection detail", request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(fail),
            base_url="http://reranker.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError) as captured:
                await HttpReranker(client, api_key=SecretStr("secret")).rerank(
                    "Requirement",
                    (RetrievalCandidate("a", "Evidence", 1, 1, 1),),
                )

        assert captured.value.code == "transport_error"
        assert "private connection detail" not in str(captured.value)

    asyncio.run(run())


def test_http_reranker_rejects_duplicate_and_missing_results() -> None:
    async def run(results: list[dict[str, object]], message: str) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "request_id": "123e4567-e89b-12d3-a456-426614174000",
                    "model": "test-reranker",
                    "model_revision": "1",
                    "device": "cpu",
                    "results": results,
                    "usage": {
                        "documents_received": 2,
                        "documents_scored": len(results),
                        "cache_hits": 0,
                        "latency_ms": 1,
                    },
                },
            )
        )
        candidates = (
            RetrievalCandidate("a", "Evidence A", 1, 1, 1),
            RetrievalCandidate("b", "Evidence B", 1, 1, 1),
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://reranker.test",
        ) as client:
            with pytest.raises(MatchingModelServiceError, match=message):
                await HttpReranker(client, api_key=SecretStr("secret")).rerank(
                    "Requirement", candidates
                )

    asyncio.run(
        run(
            [
                {"id": "a", "score": 0.8, "rank": 1},
                {"id": "a", "score": 0.7, "rank": 2},
            ],
            "duplicate",
        )
    )
    asyncio.run(run([{"id": "a", "score": 0.8, "rank": 1}], "IDs do not match"))


def test_embedding_client_does_not_receive_reranker_authorization() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "Authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "vectors": [[1.0]],
                    "model_name": "embedding",
                    "model_revision": "1",
                    "dimensions": 1,
                    "normalization_method": "l2",
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://embedding.test",
        ) as client:
            await HttpEmbeddingClient(client, dimensions=1).embed(("text",))

    asyncio.run(run())
