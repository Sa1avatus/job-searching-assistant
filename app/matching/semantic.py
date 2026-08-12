from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    model_name: str
    model_revision: str
    dimensions: int
    normalization_method: str

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ValueError("Embedding dimensions must be positive")
        if any(len(vector) != self.dimensions for vector in self.vectors):
            raise ValueError("Every embedding vector must match dimensions")


class EmbeddingClient(Protocol):
    model_name: str
    model_revision: str
    dimensions: int
    normalization_method: str

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch: ...


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    evidence_id: str
    evidence_text: str
    lexical_score: float | None
    dense_score: float | None
    hybrid_score: float

    def __post_init__(self) -> None:
        for score_name, score in (
            ("lexical_score", self.lexical_score),
            ("dense_score", self.dense_score),
            ("hybrid_score", self.hybrid_score),
        ):
            if score is not None and not 0 <= score <= 1:
                raise ValueError(f"{score_name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    candidate: RetrievalCandidate
    raw_score: float
    normalized_score: float

    def __post_init__(self) -> None:
        if not 0 <= self.normalized_score <= 1:
            raise ValueError("normalized_score must be between 0 and 1")


class Reranker(Protocol):
    model_name: str
    model_revision: str

    async def rerank(
        self,
        requirement_text: str,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RerankedCandidate, ...]: ...


class FakeEmbeddingClient:
    model_name = "fake-embedding"
    model_revision = "1"
    normalization_method = "l2"

    def __init__(self, dimensions: int = 8) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=tuple(self._vector_for_text(text) for text in texts),
            model_name=self.model_name,
            model_revision=self.model_revision,
            dimensions=self.dimensions,
            normalization_method=self.normalization_method,
        )

    def _vector_for_text(self, text: str) -> tuple[float, ...]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = [(digest[index % len(digest)] / 127.5) - 1.0 for index in range(self.dimensions)]
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0:
            values[0] = 1.0
            magnitude = 1.0
        return tuple(value / magnitude for value in values)


class FakeReranker:
    model_name = "fake-reranker"
    model_revision = "1"

    async def rerank(
        self,
        requirement_text: str,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> tuple[RerankedCandidate, ...]:
        requirement_tokens = _tokens(requirement_text)
        reranked = []
        for candidate in candidates:
            evidence_tokens = _tokens(candidate.evidence_text)
            union = requirement_tokens | evidence_tokens
            normalized_score = (
                len(requirement_tokens & evidence_tokens) / len(union) if union else 0
            )
            reranked.append(
                RerankedCandidate(
                    candidate=candidate,
                    raw_score=normalized_score,
                    normalized_score=normalized_score,
                )
            )
        return tuple(
            sorted(
                reranked,
                key=lambda reranked_candidate: (
                    reranked_candidate.normalized_score,
                    reranked_candidate.candidate.hybrid_score,
                    reranked_candidate.candidate.evidence_id,
                ),
                reverse=True,
            )
        )


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in text.split() if token.strip()}
