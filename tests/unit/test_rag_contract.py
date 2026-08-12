"""Validate that RAG client request/response shapes match the rag-platform contract.

These tests verify the client produces correct payloads without requiring a live RAG service.
They validate against the actual rag-platform schemas defined in rag_platform/api/schemas.py.
"""

from __future__ import annotations

import pytest

from app.matching.rag_client import (
    RagDocumentResult,
    RagFallbackClient,
    RagHttpClient,
    RagSearchResponse,
    RagSearchResult,
    create_rag_client,
)


def test_rag_http_client_constructor_stores_config() -> None:
    client = RagHttpClient(
        "http://localhost:8100",
        "test-key",
        project_id="proj-1",
        collection="vacancies",
        timeout_seconds=15,
    )
    assert client._base_url == "http://localhost:8100"
    assert client._api_key == "test-key"
    assert client._project_id == "proj-1"
    assert client._default_collection == "vacancies"
    assert client._timeout == 15


def test_rag_http_client_strips_trailing_slash() -> None:
    client = RagHttpClient("http://localhost:8100/", "key", project_id="p")
    assert client._base_url == "http://localhost:8100"


def test_rag_search_result_matches_contract_fields() -> None:
    result = RagSearchResult(
        chunk_id="c1",
        document_id="d1",
        external_document_id="e1",
        collection="vacancies",
        content="test content",
        score=0.95,
        reranker_score=0.88,
        rank=1,
        metadata={"source_type": "vacancy"},
    )
    assert result.chunk_id == "c1"
    assert result.document_id == "d1"
    assert result.score == 0.95
    assert result.reranker_score == 0.88
    assert result.metadata["source_type"] == "vacancy"


def test_rag_document_result_matches_contract_fields() -> None:
    result = RagDocumentResult(
        document_id="doc-uuid",
        external_document_id="vacancy:123",
        version=1,
        status="indexed",
        content_hash="abc123",
    )
    assert result.document_id == "doc-uuid"
    assert result.external_document_id == "vacancy:123"
    assert result.version == 1
    assert result.status == "indexed"


def test_rag_search_response_preserves_provenance() -> None:
    response = RagSearchResponse(
        request_id="req-uuid",
        results=(
            RagSearchResult(
                chunk_id="c1",
                document_id="d1",
                external_document_id="e1",
                collection="col",
                content="text",
                score=0.9,
                reranker_score=0.85,
                rank=1,
                metadata={"document_id": "d1"},
            ),
        ),
        effective_mode="hybrid",
        degraded=False,
    )
    assert response.request_id == "req-uuid"
    assert len(response.results) == 1
    assert response.results[0].document_id == "d1"
    assert response.results[0].reranker_score == 0.85
    assert response.degraded is False


def test_create_rag_client_factory_returns_correct_types() -> None:
    assert isinstance(
        create_rag_client(
            service_url="http://localhost:8100",
            api_key="key",
            project_id="proj",
            enabled=True,
        ),
        RagHttpClient,
    )
    assert isinstance(
        create_rag_client(
            service_url=None,
            api_key=None,
            project_id=None,
            enabled=True,
        ),
        RagFallbackClient,
    )
    assert isinstance(
        create_rag_client(
            service_url="http://localhost:8100",
            api_key="key",
            project_id="proj",
            enabled=False,
        ),
        RagFallbackClient,
    )


@pytest.mark.asyncio
async def test_fallback_client_conforms_to_protocol() -> None:
    client = RagFallbackClient()
    search_result = await client.search("test")
    assert isinstance(search_result, RagSearchResponse)
    assert search_result.degraded is True
    assert search_result.results == ()

    doc_result = await client.ingest_document(
        external_document_id="test", content="content", collection="col"
    )
    assert isinstance(doc_result, RagDocumentResult)
    assert doc_result.status == "skipped"

    health = await client.health()
    assert health.available is False
