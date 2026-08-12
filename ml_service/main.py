from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_MAX_TEXTS = 128
_MAX_TEXT_CHARACTERS = 8_000
_MAX_PAIRS = 128


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
            "RERANKER_MODEL_NAME", "cross-encoder/ettin-reranker-68m-v1"
        )
        self.reranker_model_revision = os.getenv("RERANKER_MODEL_REVISION", "main")
        self.device = os.getenv("MODEL_DEVICE", "cpu")
        self.batch_size = int(os.getenv("MODEL_BATCH_SIZE", "8"))
        self.embedding_model: Any = None
        self.reranker_model: Any = None

    def load(self) -> None:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        cache_dir = os.getenv("HF_HUB_CACHE")
        self.embedding_model = SentenceTransformer(
            self.embedding_model_name,
            revision=self.embedding_model_revision,
            cache_folder=cache_dir,
            device=self.device,
        )
        self.reranker_model = CrossEncoder(
            self.reranker_model_name,
            revision=self.reranker_model_revision,
            automodel_args={"trust_remote_code": True},
            device=self.device,
        )

    @property
    def is_loaded(self) -> bool:
        return self.embedding_model is not None and self.reranker_model is not None


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
    if runtime.reranker_model is None:
        raise HTTPException(status_code=503, detail="Reranker model is not loaded")
    pairs = [
        [_bounded_text(pair.requirement), _bounded_text(pair.evidence)]
        for pair in request.pairs
    ]
    raw_scores = runtime.reranker_model.predict(
        pairs,
        batch_size=runtime.batch_size,
        show_progress_bar=False,
    )
    raw_scores = [float(s) for s in raw_scores]
    scores = [
        RerankScore(
            raw_score=float(raw_score),
            normalized_score=_sigmoid(float(raw_score)),
        )
        for raw_score in raw_scores
    ]
    return RerankResponse(
        scores=scores,
        model_name=runtime.reranker_model_name,
        model_revision=runtime.reranker_model_revision,
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
