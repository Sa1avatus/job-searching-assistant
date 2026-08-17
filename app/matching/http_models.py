from __future__ import annotations

import time
from uuid import UUID

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.matching.semantic import (
    EmbeddingBatch,
    RerankedCandidate,
    RetrievalCandidate,
)

logger = structlog.get_logger(__name__)


class MatchingModelServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    model_name: str
    model_revision: str
    dimensions: int = Field(gt=0)
    normalization_method: str


class _RerankResult(BaseModel):
    id: str
    score: float
    normalized_score: float | None = Field(default=None, ge=0, le=1)
    rank: int = Field(ge=1)
    text: str | None = None
    metadata: dict[str, object] | None = None
    token_count: int | None = None
    truncated: bool = False
    cache_hit: bool = False


class _RerankUsage(BaseModel):
    documents_received: int = Field(ge=0)
    documents_scored: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class _RerankResponse(BaseModel):
    request_id: UUID
    model: str
    model_revision: str
    device: str
    requested_revision: str | None = None
    resolved_revision: str | None = None
    backend: str | None = None
    rerank_mode: str | None = None
    active_provider: str | None = None
    results: list[_RerankResult]
    usage: _RerankUsage


class HttpEmbeddingClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        model_name: str = "intfloat/multilingual-e5-small",
        model_revision: str = "main",
        dimensions: int = 384,
    ) -> None:
        self._http_client = http_client
        self.model_name = model_name
        self.model_revision = model_revision
        self.dimensions = dimensions
        self.normalization_method = "l2"

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(
                vectors=(),
                model_name=self.model_name,
                model_revision=self.model_revision,
                dimensions=self.dimensions,
                normalization_method=self.normalization_method,
            )
        start = time.monotonic()
        response = await self._http_client.post("/v1/embeddings", json={"texts": list(texts)})
        duration_s = round(time.monotonic() - start, 3)
        _require_success(response, operation="embedding")
        logger.debug(
            "embedding_request",
            count=len(texts),
            status_code=response.status_code,
            duration_s=duration_s,
        )
        payload = _EmbeddingResponse.model_validate(response.json())
        if len(payload.vectors) != len(texts):
            raise MatchingModelServiceError("Embedding response count does not match request")
        if payload.dimensions != self.dimensions:
            raise MatchingModelServiceError(
                f"Embedding service returned {payload.dimensions} dimensions, "
                f"expected {self.dimensions}"
            )
        return EmbeddingBatch(
            vectors=tuple(tuple(vector) for vector in payload.vectors),
            model_name=payload.model_name,
            model_revision=payload.model_revision,
            dimensions=payload.dimensions,
            normalization_method=payload.normalization_method,
        )


class HttpReranker:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        model_name: str = "external-reranker",
        model_revision: str = "unresolved",
        api_key: SecretStr,
    ) -> None:
        self._http_client = http_client
        self.model_name = model_name
        self.model_revision = model_revision
        self._api_key = api_key

    async def rerank(
        self,
        requirement_text: str,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RerankedCandidate, ...]:
        if not candidates:
            return ()
        candidate_by_id = {candidate.evidence_id: candidate for candidate in candidates}
        if len(candidate_by_id) != len(candidates):
            raise MatchingModelServiceError("Reranker candidates must have unique evidence IDs")
        start = time.monotonic()
        try:
            response = await self._http_client.post(
                "/v1/rerank",
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                json={
                    "query": requirement_text,
                    "documents": [
                        {
                            "id": candidate.evidence_id,
                            "text": candidate.evidence_text,
                            "metadata": {},
                        }
                        for candidate in candidates
                    ],
                    "top_n": len(candidates),
                    "return_documents": False,
                    "truncate": True,
                },
            )
        except httpx.TimeoutException as error:
            raise MatchingModelServiceError("Reranker request timed out", code="timeout") from error
        except httpx.RequestError as error:
            raise MatchingModelServiceError(
                "Reranker transport failed", code="transport_error"
            ) from error
        _require_success(response, operation="reranking", include_body=False)
        duration_s = round(time.monotonic() - start, 3)
        logger.debug(
            "reranker_request",
            candidates=len(candidates),
            status_code=response.status_code,
            duration_s=duration_s,
        )
        try:
            payload = _RerankResponse.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise MatchingModelServiceError(
                "Reranker response does not match the public contract",
                code="contract_mismatch",
            ) from error
        result_by_id = {result.id: result for result in payload.results}
        if len(result_by_id) != len(payload.results):
            raise MatchingModelServiceError("Reranker response contains duplicate evidence IDs")
        expected_ids = set(candidate_by_id)
        actual_ids = set(result_by_id)
        if actual_ids != expected_ids:
            raise MatchingModelServiceError("Reranker response evidence IDs do not match request")
        self.model_name = payload.model
        self.model_revision = payload.resolved_revision or payload.model_revision
        normalized_by_id: dict[str, float] = {}
        for result in payload.results:
            normalized = result.normalized_score
            if normalized is None and 0 <= result.score <= 1:
                normalized = result.score
            if normalized is None:
                raise MatchingModelServiceError(
                    "Reranker returned an unbounded score without normalized_score",
                    code="contract_mismatch",
                )
            normalized_by_id[result.id] = normalized
        reranked = tuple(
            (
                RerankedCandidate(
                    candidate=candidate_by_id[result.id],
                    raw_score=result.score,
                    normalized_score=normalized_by_id[result.id],
                ),
                result.rank,
            )
            for result in payload.results
        )
        return tuple(
            item
            for item, _ in sorted(
                reranked,
                key=lambda pair: (
                    -pair[0].normalized_score,
                    pair[1],
                    pair[0].candidate.evidence_id,
                ),
            )
        )


class UnavailableReranker:
    model_name = "external-reranker"
    model_revision = "unconfigured"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def rerank(
        self,
        requirement_text: str,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RerankedCandidate, ...]:
        raise MatchingModelServiceError(self._reason, code="configuration_error")


def _require_success(
    response: httpx.Response,
    *,
    operation: str,
    include_body: bool = True,
) -> None:
    if response.status_code >= 400:
        detail = f": {response.text[:500]}" if include_body else ""
        code = {
            401: "authentication_error",
            403: "authentication_error",
            422: "contract_mismatch",
            429: "rate_limited",
            503: "service_unavailable",
        }.get(response.status_code, "provider_error")
        raise MatchingModelServiceError(
            f"Matching model {operation} failed with HTTP {response.status_code}{detail}",
            code=code,
        )
