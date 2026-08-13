import httpx
import pytest

from app.matching.rag_client import (
    RagFallbackClient,
    RagHttpClient,
    RagSearchResponse,
    RagSearchResult,
    create_rag_client,
)


@pytest.mark.asyncio
async def test_fallback_client_returns_empty_results() -> None:
    client = RagFallbackClient()

    result = await client.search("test query", owner_user_id="owner-1")

    assert result.results == ()
    assert result.degraded is True
    assert result.effective_mode == "fallback"


@pytest.mark.asyncio
async def test_fallback_client_reports_unavailable() -> None:
    client = RagFallbackClient()

    health = await client.health()

    assert health.available is False
    assert health.status == "not_configured"


def test_create_rag_client_returns_fallback_when_disabled() -> None:
    client = create_rag_client(
        service_url="http://localhost:8100",
        api_key="test-key",
        project_id="proj-1",
        enabled=False,
    )

    assert isinstance(client, RagFallbackClient)


def test_create_rag_client_returns_fallback_when_missing_config() -> None:
    client = create_rag_client(
        service_url=None,
        api_key=None,
        project_id=None,
        enabled=True,
    )

    assert isinstance(client, RagFallbackClient)


def test_create_rag_client_returns_http_client_when_configured() -> None:
    client = create_rag_client(
        service_url="http://localhost:8100",
        api_key="test-key",
        project_id="proj-1",
        enabled=True,
    )

    assert isinstance(client, RagHttpClient)


def test_rag_search_result_fields() -> None:
    result = RagSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        external_document_id="ext-1",
        collection="default",
        content="test content",
        score=0.95,
        reranker_score=0.88,
        rank=1,
        metadata={"source": "test"},
    )

    assert result.chunk_id == "chunk-1"
    assert result.score == 0.95
    assert result.reranker_score == 0.88
    assert result.metadata == {"source": "test"}


def test_rag_search_response_provenance() -> None:
    response = RagSearchResponse(
        request_id="req-1",
        results=(
            RagSearchResult(
                chunk_id="c1",
                document_id="d1",
                external_document_id="e1",
                collection="col",
                content="text",
                score=0.9,
                reranker_score=None,
                rank=1,
                metadata={},
            ),
        ),
        effective_mode="hybrid",
        degraded=False,
    )

    assert response.request_id == "req-1"
    assert len(response.results) == 1
    assert response.results[0].document_id == "d1"
    assert response.degraded is False


@pytest.mark.asyncio
async def test_http_client_sends_owner_header_for_search(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            del args

        async def post(self, url, *, json, headers):
            requests.append({"url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"request_id": "req-1", "results": [], "trace": {}},
            )

    monkeypatch.setattr("app.matching.rag_client.httpx.AsyncClient", RecordingClient)
    client = RagHttpClient(
        "http://rag.test",
        "test-key",
        project_id="project-1",
    )

    await client.search("Python", owner_user_id="owner-1")

    assert requests[0]["headers"] == {
        "Authorization": "Bearer test-key",
        "X-Owner-User-Id": "owner-1",
    }
    assert "metadata_filter" not in requests[0]["json"]


@pytest.mark.asyncio
async def test_http_client_sends_owner_header_for_ingestion(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            del args

        async def post(self, url, *, json, headers):
            requests.append({"url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "doc-1",
                    "external_document_id": "cv-1",
                    "version": 1,
                    "status": "indexed",
                    "content_hash": "hash",
                },
            )

    monkeypatch.setattr("app.matching.rag_client.httpx.AsyncClient", RecordingClient)
    client = RagHttpClient(
        "http://rag.test",
        "test-key",
        project_id="project-1",
    )

    await client.ingest_document(
        owner_user_id="owner-1",
        external_document_id="cv-1",
        content="Python",
        collection="profiles",
    )

    assert requests[0]["headers"] == {
        "Authorization": "Bearer test-key",
        "X-Owner-User-Id": "owner-1",
    }
