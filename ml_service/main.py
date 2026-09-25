from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_MAX_TEXTS = 128
_MAX_TEXT_CHARACTERS = 8_000
_MAX_PAIRS = 128

RerankScoringMode = Literal["cross_encoder", "e5_cosine"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmbeddingRequest(StrictModel):
    texts: list[str] = Field(min_length=1, max_length=_MAX_TEXTS)


class EmbeddingResponse(StrictModel):
    vectors: list[list[float]]
    model_name: str
    model_revision: str
    dimensions: int
    normalization_method: str


class RerankPair(StrictModel):
    requirement: str = Field(min_length=1, max_length=_MAX_TEXT_CHARACTERS)
    evidence: str = Field(min_length=1, max_length=_MAX_TEXT_CHARACTERS)


class RerankRequest(StrictModel):
    pairs: list[RerankPair] = Field(min_length=1, max_length=_MAX_PAIRS)
    # Per-request override of RERANK_SCORING_MODE, for A/B comparisons against the
    # running container without a restart. Omitted, it falls back to the env default.
    scoring_mode: RerankScoringMode | None = None


class RerankScore(StrictModel):
    raw_score: float
    normalized_score: float = Field(ge=0, le=1)


class RerankResponse(StrictModel):
    scores: list[RerankScore]
    model_name: str
    model_revision: str


class ModelRuntime:
    def __init__(self) -> None:
        self.embedding_model_name = os.getenv(
            "EMBEDDING_MODEL_NAME", "intfloat/multilingual-e5-small"
        )
        self.embedding_model_revision = os.getenv("EMBEDDING_MODEL_REVISION", "main")
        self.reranker_model_name = os.getenv(
            "RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-2-v2"
        )
        self.reranker_model_revision = os.getenv("RERANKER_MODEL_REVISION", "main")
        self.device = os.getenv("MODEL_DEVICE", "cpu")
        self.batch_size = int(os.getenv("MODEL_BATCH_SIZE", "8"))
        # A deployment that only needs /v1/rerank (e.g. a cheap CPU-only reranker
        # alongside an embedding model already served elsewhere) can skip loading
        # the embedding model entirely, instead of loading it unused.
        self.load_embedding_model = os.getenv("LOAD_EMBEDDING_MODEL", "true").casefold() not in (
            "false",
            "0",
            "no",
        )
        default_mode = os.getenv("RERANK_SCORING_MODE", "cross_encoder")
        if default_mode not in ("cross_encoder", "e5_cosine"):
            raise ValueError(
                f"RERANK_SCORING_MODE must be cross_encoder or e5_cosine, got {default_mode!r}"
            )
        self.default_scoring_mode: RerankScoringMode = default_mode  # type: ignore[assignment]
        self.embedding_model: Any = None
        self.reranker_model: Any = None

    def load(self) -> None:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        cache_dir = os.getenv("HF_HUB_CACHE")
        if self.load_embedding_model:
            self.embedding_model = SentenceTransformer(
                self.embedding_model_name,
                revision=self.embedding_model_revision,
                cache_folder=cache_dir,
                device=self.device,
            )
        self.reranker_model = CrossEncoder(
            self.reranker_model_name,
            revision=self.reranker_model_revision,
            device=self.device,
        )

    @property
    def is_loaded(self) -> bool:
        if self.load_embedding_model and self.embedding_model is None:
            return False
        return self.reranker_model is not None


runtime = ModelRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    runtime.load()
    yield


app = FastAPI(title="Matching Model Service", version="1", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    if not runtime.is_loaded:
        raise HTTPException(status_code=503, detail="Models are not loaded")
    return {
        "status": "ok",
        "embedding_model": runtime.embedding_model_name,
        "embedding_revision": runtime.embedding_model_revision,
        "reranker_model": runtime.reranker_model_name,
        "reranker_revision": runtime.reranker_model_revision,
        "default_scoring_mode": runtime.default_scoring_mode,
    }


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
def embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    if runtime.embedding_model is None:
        raise HTTPException(status_code=503, detail="Embedding model is not loaded")
    texts = [_bounded_text(text) for text in request.texts]
    # E5 models expect "query: " prefix for queries, but we use raw texts
    # since this service embeds both queries and documents uniformly.
    output = runtime.embedding_model.encode(
        texts,
        batch_size=runtime.batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    vectors = output.tolist()
    dimensions = len(vectors[0])
    return EmbeddingResponse(
        vectors=vectors,
        model_name=runtime.embedding_model_name,
        model_revision=runtime.embedding_model_revision,
        dimensions=dimensions,
        normalization_method="l2",
    )


@app.post("/v1/rerank", response_model=RerankResponse)
def rerank(request: RerankRequest) -> RerankResponse:
    mode = request.scoring_mode or runtime.default_scoring_mode
    if mode == "e5_cosine":
        return _rerank_e5_cosine(request.pairs)
    return _rerank_cross_encoder(request.pairs)


def _rerank_cross_encoder(pairs: list[RerankPair]) -> RerankResponse:
    if runtime.reranker_model is None:
        raise HTTPException(status_code=503, detail="Reranker model is not loaded")
    text_pairs = [[_bounded_text(pair.requirement), _bounded_text(pair.evidence)] for pair in pairs]
    raw_scores = runtime.reranker_model.predict(
        text_pairs,
        batch_size=runtime.batch_size,
        show_progress_bar=False,
    )
    scores = [
        RerankScore(raw_score=float(raw_score), normalized_score=_sigmoid(float(raw_score)))
        for raw_score in raw_scores
    ]
    return RerankResponse(
        scores=scores,
        model_name=runtime.reranker_model_name,
        model_revision=runtime.reranker_model_revision,
    )


def _rerank_e5_cosine(pairs: list[RerankPair]) -> RerankResponse:
    if runtime.embedding_model is None:
        raise HTTPException(
            status_code=503,
            detail="e5_cosine scoring requires the embedding model, which is not loaded "
            "(LOAD_EMBEDDING_MODEL=false)",
        )
    # requirement=vacancy text gets the "passage: " prefix, evidence=resume text gets
    # "query: " -- matching the exact convention this deployment was asked to test, not the
    # usual E5 role assignment. Texts are deduped so a request scoring one resume against many
    # vacancies (or vice versa) embeds each unique string once, not once per pair.
    unique_texts: dict[str, str] = {}
    for pair in pairs:
        requirement = _bounded_text(pair.requirement)
        evidence = _bounded_text(pair.evidence)
        unique_texts.setdefault(requirement, f"passage: {requirement}")
        unique_texts.setdefault(evidence, f"query: {evidence}")
    keys = list(unique_texts)
    vectors = runtime.embedding_model.encode(
        [unique_texts[key] for key in keys],
        batch_size=runtime.batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    vector_by_text = dict(zip(keys, vectors, strict=True))
    scores = []
    for pair in pairs:
        requirement = _bounded_text(pair.requirement)
        evidence = _bounded_text(pair.evidence)
        cosine = float(vector_by_text[requirement] @ vector_by_text[evidence])
        cosine = max(-1.0, min(1.0, cosine))
        scores.append(RerankScore(raw_score=cosine, normalized_score=(cosine + 1.0) / 2.0))
    return RerankResponse(
        scores=scores,
        model_name=runtime.embedding_model_name,
        model_revision=runtime.embedding_model_revision,
    )


def _bounded_text(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="Text must not be blank")
    return normalized[:_MAX_TEXT_CHARACTERS]


def _sigmoid(score: float) -> float:
    if score >= 0:
        return 1 / (1 + math.exp(-score))
    exponential = math.exp(score)
    return exponential / (1 + exponential)
