from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    chunk_id: str
    document_id: str
    external_document_id: str
    collection: str
    content: str
    score: float
    reranker_score: float | None
    rank: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RagSearchResponse:
    request_id: str
    results: tuple[RagSearchResult, ...]
    effective_mode: str
    degraded: bool


@dataclass(frozen=True, slots=True)
class RagHealthStatus:
    available: bool
    status: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RagDocumentResult:
    document_id: str
    external_document_id: str
    version: int
    status: str
    content_hash: str


class RagClient(Protocol):
    async def search(
        self,
        query: str,
        *,
        collections: tuple[str, ...] | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
    ) -> RagSearchResponse: ...

    async def ingest_document(
        self,
        *,
        external_document_id: str,
        content: str,
        collection: str,
        title: str = "",
        document_type: str = "text",
        language: str = "und",
        version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> RagDocumentResult: ...

    async def health(self) -> RagHealthStatus: ...


class RagHttpClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        project_id: str,
        collection: str = "default",
        timeout_seconds: float = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._project_id = project_id
        self._default_collection = collection
        self._timeout = timeout_seconds

    async def search(
        self,
        query: str,
        *,
        collections: tuple[str, ...] | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
    ) -> RagSearchResponse:
        if collections is None:
            collections = (self._default_collection,)
        payload = {
            "project_id": self._project_id,
            "collections": list(collections),
            "query": query,
            "mode": mode,
            "fusion_top_k": top_k,
            "rerank_top_k": min(top_k, 10),
            "use_reranker": True,
            "include_trace": False,
        }
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/retrieval/search",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
        duration = time.perf_counter() - started
        data = response.json()
        results = tuple(
            RagSearchResult(
                chunk_id=str(item.get("chunk_id", "")),
                document_id=str(item.get("document_id", "")),
                external_document_id=str(
                    item.get("external_document_id", "")
                ),
                collection=str(item.get("collection", "")),
                content=str(item.get("content", "")),
                score=float(item.get("score", 0)),
                reranker_score=(
                    float(item["reranker_score"])
                    if item.get("reranker_score") is not None
                    else None
                ),
                rank=int(item.get("rank", 0)),
                metadata=dict(item.get("metadata", {})),
            )
            for item in data.get("results", [])
        )
        trace = data.get("trace") or {}
        logger.info(
            "rag_search_completed",
            request_id=data.get("request_id"),
            result_count=len(results),
            effective_mode=trace.get("effective_mode", mode),
            degraded=trace.get("degraded", False),
            duration_seconds=round(duration, 3),
        )
        return RagSearchResponse(
            request_id=str(data.get("request_id", "")),
            results=results,
            effective_mode=str(trace.get("effective_mode", mode)),
            degraded=bool(trace.get("degraded", False)),
        )

    async def ingest_document(
        self,
        *,
        external_document_id: str,
        content: str,
        collection: str,
        title: str = "",
        document_type: str = "text",
        language: str = "und",
        version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> RagDocumentResult:
        payload = {
            "project_id": self._project_id,
            "collection": collection,
            "external_document_id": external_document_id,
            "content": content,
            "document_type": document_type,
            "title": title,
            "language": language,
            "version": version,
            "metadata": metadata or {},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/documents",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
        data = response.json()
        logger.info(
            "rag_document_ingested",
            document_id=data.get("id"),
            external_document_id=external_document_id,
            collection=collection,
            status=data.get("status"),
        )
        return RagDocumentResult(
            document_id=str(data.get("id", "")),
            external_document_id=str(
                data.get("external_document_id", external_document_id)
            ),
            version=int(data.get("version", version)),
            status=str(data.get("status", "unknown")),
            content_hash=str(data.get("content_hash", "")),
        )

    async def health(self) -> RagHealthStatus:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self._base_url}/v1/admin/system/health",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                return RagHealthStatus(
                    available=True,
                    status=str(data.get("status", "unknown")),
                    details=data,
                )
        except Exception as error:
            return RagHealthStatus(
                available=False,
                status=f"{type(error).__name__}: {error}",
                details={},
            )


class RagFallbackClient:
    """Returns empty results when RAG is unavailable or not configured."""

    async def search(
        self,
        query: str,
        *,
        collections: tuple[str, ...] | None = None,
        mode: str = "hybrid",
        top_k: int = 10,
    ) -> RagSearchResponse:
        del query, collections, mode, top_k
        return RagSearchResponse(
            request_id="fallback",
            results=(),
            effective_mode="fallback",
            degraded=True,
        )

    async def ingest_document(
        self,
        *,
        external_document_id: str,
        content: str,
        collection: str,
        title: str = "",
        document_type: str = "text",
        language: str = "und",
        version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> RagDocumentResult:
        del content, collection, title, document_type, language, version, metadata
        return RagDocumentResult(
            document_id="",
            external_document_id=external_document_id,
            version=0,
            status="skipped",
            content_hash="",
        )

    async def health(self) -> RagHealthStatus:
        return RagHealthStatus(
            available=False,
            status="not_configured",
            details={},
        )


def create_rag_client(
    *,
    service_url: str | None,
    api_key: str | None,
    project_id: str | None,
    collection: str = "default",
    timeout_seconds: float = 30,
    enabled: bool = False,
) -> RagClient:
    if not enabled or not service_url or not api_key or not project_id:
        logger.info("rag_client_using_fallback", enabled=enabled, configured=bool(service_url))
        return RagFallbackClient()
    return RagHttpClient(
        service_url,
        api_key,
        project_id=project_id,
        collection=collection,
        timeout_seconds=timeout_seconds,
    )
