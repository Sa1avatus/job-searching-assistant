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


@pytest.mark.asyncio
async def test_http_client_updates_existing_document_after_version_conflict(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            del args

        async def post(self, url, *, json, headers):
            requests.append({"method": "POST", "url": url, "json": json, "headers": headers})
            return httpx.Response(409, request=httpx.Request("POST", url))

        async def get(self, url, *, params, headers):
            requests.append({"method": "GET", "url": url, "params": params, "headers": headers})
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json=[
                    {
                        "id": "doc-1",
                        "external_document_id": "profile:owner-1",
                        "lock_version": 3,
                    }
                ],
            )

        async def patch(self, url, *, json, headers):
            requests.append({"method": "PATCH", "url": url, "json": json, "headers": headers})
            return httpx.Response(
                202,
                request=httpx.Request("PATCH", url),
                json={
                    "id": "doc-1",
                    "external_document_id": "profile:owner-1",
                    "version": 4,
                    "status": "queued",
                    "content_hash": "new-hash",
                },
            )

    monkeypatch.setattr("app.matching.rag_client.httpx.AsyncClient", RecordingClient)
    client = RagHttpClient("http://rag.test", "test-key", project_id="project-1")

    result = await client.ingest_document(
        owner_user_id="owner-1",
        external_document_id="profile:owner-1",
        content="Updated Python experience",
        collection="profiles",
        title="Profile",
        metadata={"source_type": "profile"},
    )

    assert result.version == 4
    assert [request["method"] for request in requests] == ["POST", "GET", "PATCH"]
    assert requests[2]["url"] == "http://rag.test/v1/documents/doc-1"
    assert requests[2]["json"] == {
        "expected_lock_version": 3,
        "content": "Updated Python experience",
        "title": "Profile",
        "document_type": "text",
        "language": "und",
        "metadata": {"source_type": "profile"},
    }


@pytest.mark.asyncio
async def test_http_client_retries_optimistic_lock_conflict_at_most_three_times(
    monkeypatch,
) -> None:
    patch_attempts = 0

    class ConflictingClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            del args

        async def post(self, url, *, json, headers):
            del json, headers
            return httpx.Response(409, request=httpx.Request("POST", url))

        async def get(self, url, *, params, headers):
            del params, headers
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json=[
                    {
                        "id": "doc-1",
                        "external_document_id": "profile:owner-1",
                        "lock_version": patch_attempts + 1,
                    }
                ],
            )

        async def patch(self, url, *, json, headers):
            nonlocal patch_attempts
            del json, headers
            patch_attempts += 1
            return httpx.Response(409, request=httpx.Request("PATCH", url))

    monkeypatch.setattr("app.matching.rag_client.httpx.AsyncClient", ConflictingClient)
    client = RagHttpClient("http://rag.test", "test-key", project_id="project-1")

    with pytest.raises(httpx.HTTPStatusError):
        await client.ingest_document(
            owner_user_id="owner-1",
            external_document_id="profile:owner-1",
            content="Updated",
            collection="profiles",
        )

    assert patch_attempts == 3


@pytest.mark.asyncio
async def test_http_client_deletes_owner_scoped_document_by_external_id(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    class RecordingClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            del args

        async def get(self, url, *, params, headers):
            requests.append({"method": "GET", "url": url, "params": params, "headers": headers})
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json=[
                    {
                        "id": "doc-1",
                        "external_document_id": "cv:other",
                    },
                    {
                        "id": "doc-2",
                        "external_document_id": "cv:target",
                    },
                ],
            )

        async def delete(self, url, *, headers):
            requests.append({"method": "DELETE", "url": url, "headers": headers})
            return httpx.Response(204, request=httpx.Request("DELETE", url))

    monkeypatch.setattr("app.matching.rag_client.httpx.AsyncClient", RecordingClient)
    client = RagHttpClient("http://rag.test", "test-key", project_id="project-1")

    deleted = await client.delete_document(
        owner_user_id="owner-1",
        external_document_id="cv:target",
        collection="resumes",
    )

    assert deleted is True
    assert requests[0]["params"] == {
        "project_id": "project-1",
        "collection": "resumes",
        "limit": 200,
        "offset": 0,
    }
    assert requests[0]["headers"] == {
        "Authorization": "Bearer test-key",
        "X-Owner-User-Id": "owner-1",
    }
    assert requests[1]["url"] == "http://rag.test/v1/documents/doc-2"


@pytest.mark.asyncio
async def test_fallback_client_skips_document_deletion() -> None:
    client = RagFallbackClient()

    deleted = await client.delete_document(
        owner_user_id="owner-1",
        external_document_id="profile:owner-1",
        collection="profiles",
    )

    assert deleted is False
