from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.matching.semantic import (
    EmbeddingBatch,
    RerankedCandidate,
    RetrievalCandidate,
)


class MatchingModelServiceError(RuntimeError):
    pass


class _StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EmbeddingResponse(_StrictResponse):
    vectors: list[list[float]]
    model_name: str
    model_revision: str
    dimensions: int = Field(gt=0)
    normalization_method: str


class _RerankScore(_StrictResponse):
    raw_score: float
    normalized_score: float = Field(ge=0, le=1)


class _RerankResponse(_StrictResponse):
    scores: list[_RerankScore]
    model_name: str
    model_revision: str


class HttpEmbeddingClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        model_name: str = "BAAI/bge-m3",
        model_revision: str = "main",
        dimensions: int = 1024,
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
        response = await self._http_client.post("/v1/embeddings", json={"texts": list(texts)})
        _require_success(response, operation="embedding")
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
        model_name: str = "BAAI/bge-reranker-v2-m3",
        model_revision: str = "main",
    ) -> None:
        self._http_client = http_client
        self.model_name = model_name
        self.model_revision = model_revision

    async def rerank(
        self,
        requirement_text: str,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RerankedCandidate, ...]:
        if not candidates:
            return ()
        response = await self._http_client.post(
            "/v1/rerank",
            json={
                "pairs": [
                    {
                        "requirement": requirement_text,
                        "evidence": candidate.evidence_text,
                    }
                    for candidate in candidates
                ]
            },
        )
        _require_success(response, operation="reranking")
        payload = _RerankResponse.model_validate(response.json())
        if len(payload.scores) != len(candidates):
            raise MatchingModelServiceError("Reranker response count does not match request")
        self.model_name = payload.model_name
        self.model_revision = payload.model_revision
        reranked = tuple(
            RerankedCandidate(
                candidate=candidate,
                raw_score=score.raw_score,
                normalized_score=score.normalized_score,
            )
            for candidate, score in zip(candidates, payload.scores, strict=True)
        )
        return tuple(
            sorted(
                reranked,
                key=lambda item: (
                    item.normalized_score,
                    item.candidate.hybrid_score,
                    item.candidate.evidence_id,
                ),
                reverse=True,
            )
        )


def _require_success(response: httpx.Response, *, operation: str) -> None:
    if response.status_code >= 400:
        raise MatchingModelServiceError(
            f"Matching model {operation} failed with HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
