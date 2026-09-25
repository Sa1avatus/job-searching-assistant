"""Shadow-mode e5-small scorer for job_discovery's keyword score (Settings.matching_scorer).

keyword (default): this module is never called - zero behavior or performance change.
shadow: compute_shadow_score runs alongside _rescore_from_text; its result is only logged
    (structlog JSON, event="matching_shadow_score"), never written to ApplicationRow.match_score
    or used for ranking.
e5: same computation, but intended for the caller to rank/score by instead of keyword. Not
    enabled by this change - implemented so the switch itself is testable end to end.

Embeddings are cached in matching_embedding_cache by (entity_type, entity_id, model_name,
model_revision, content_hash). A cache write only happens when the live embedding service's
response confirms it answered with the pinned `matching_scorer_embedding_model_name` - a model
swap behind the same URL without updating that setting can't silently poison the cache.

Vacancies have no pre-existing dense-retrieval cache to reuse: only candidate evidence is
embedded and indexed for matching v2 (app/matching/indexing.py, entity_type="candidate_evidence"
in EmbeddingRecordRow, which registers what got indexed into OpenSearch but does not itself store
vectors). This module stores vectors directly instead.

compute_shadow_score takes a caller-owned httpx.AsyncClient rather than opening one per call:
on this deployment, a fresh client's connection setup alone measured ~500-1000ms (Docker Desktop
on Windows port forwarding), dwarfing the ~20ms the embedding model actually takes in-process.
Callers must create one client per batch (e.g. one per discovery request) and reuse it.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.storage.tables import MatchingEmbeddingCacheRow

logger = structlog.get_logger(__name__)

_VACANCY_PREFIX = "passage: "
_RESUME_PREFIX = "query: "


@dataclass(frozen=True)
class ShadowScoreResult:
    e5_score: float | None  # cosine similarity in [-1, 1]; None means fallback happened
    latency_ms: float
    used_fallback: bool
    vacancy_cache_hit: bool = False
    resume_cache_hit: bool = False


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lookup_vector(
    session: Session, *, entity_type: str, entity_id: str, content_hash: str, model_name: str
) -> list[float] | None:
    row = session.scalar(
        select(MatchingEmbeddingCacheRow).where(
            MatchingEmbeddingCacheRow.entity_type == entity_type,
            MatchingEmbeddingCacheRow.entity_id == entity_id,
            MatchingEmbeddingCacheRow.content_hash == content_hash,
            MatchingEmbeddingCacheRow.model_name == model_name,
        )
    )
    return list(row.vector) if row is not None else None


def _store_vector(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    content_hash: str,
    model_name: str,
    model_revision: str,
    vector: list[float],
) -> None:
    existing = session.scalar(
        select(MatchingEmbeddingCacheRow).where(
            MatchingEmbeddingCacheRow.entity_type == entity_type,
            MatchingEmbeddingCacheRow.entity_id == entity_id,
            MatchingEmbeddingCacheRow.content_hash == content_hash,
            MatchingEmbeddingCacheRow.model_name == model_name,
            MatchingEmbeddingCacheRow.model_revision == model_revision,
        )
    )
    if existing is not None:
        return
    session.add(
        MatchingEmbeddingCacheRow(
            entity_type=entity_type,
            entity_id=entity_id,
            content_hash=content_hash,
            model_name=model_name,
            model_revision=model_revision,
            dimensions=len(vector),
            vector=vector,
        )
    )
    session.commit()


async def _get_or_embed(
    session: Session,
    client: httpx.AsyncClient,
    *,
    base_url: str,
    entity_type: str,
    entity_id: str,
    text: str,
    prefix: str,
    expected_model_name: str,
    timeout: float,
) -> tuple[list[float], bool]:
    """Returns (vector, cache_hit). Raises on transport/contract failure; the caller falls back."""
    content_hash = _content_hash(text)
    cached = _lookup_vector(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        content_hash=content_hash,
        model_name=expected_model_name,
    )
    if cached is not None:
        return cached, True

    response = await client.post(
        f"{base_url}/v1/embeddings", json={"texts": [prefix + text]}, timeout=timeout
    )
    response.raise_for_status()
    payload = response.json()
    vector = payload["vectors"][0]
    model_name = payload["model_name"]
    model_revision = payload["model_revision"]
    if model_name == expected_model_name:
        _store_vector(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            content_hash=content_hash,
            model_name=model_name,
            model_revision=model_revision,
            vector=vector,
        )
    else:
        logger.warning(
            "matching_shadow_score_model_mismatch",
            expected_model=expected_model_name,
            actual_model=model_name,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    return vector, False


async def compute_shadow_score(
    session: Session,
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    vacancy_id: str,
    vacancy_text: str,
    resume_id: str,
    resume_text: str,
) -> ShadowScoreResult:
    """`client` must be a caller-owned, reused httpx.AsyncClient - not one created per call.

    A fresh httpx.AsyncClient() per call was measured at ~500-1000ms just for connection
    setup on this deployment's Docker-Desktop-on-Windows port forwarding, versus ~30-50ms
    reusing a warm connection. That gap, not embedding compute (~20ms in-process) or torch
    thread count, is what a naive per-call client would show up as "suspiciously slow".
    """
    start = time.monotonic()
    base_url = settings.resolved_embedding_service_url
    timeout = settings.matching_scorer_embedding_timeout_seconds
    model_name = settings.matching_scorer_embedding_model_name
    try:
        vacancy_vector, vacancy_hit = await _get_or_embed(
            session,
            client,
            base_url=base_url,
            entity_type="vacancy",
            entity_id=vacancy_id,
            text=vacancy_text,
            prefix=_VACANCY_PREFIX,
            expected_model_name=model_name,
            timeout=timeout,
        )
        resume_vector, resume_hit = await _get_or_embed(
            session,
            client,
            base_url=base_url,
            entity_type="resume",
            entity_id=resume_id,
            text=resume_text,
            prefix=_RESUME_PREFIX,
            expected_model_name=model_name,
            timeout=timeout,
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as error:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning(
            "matching_shadow_score_fallback",
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            error=str(error),
            latency_ms=round(latency_ms, 1),
        )
        return ShadowScoreResult(e5_score=None, latency_ms=round(latency_ms, 1), used_fallback=True)

    cosine = sum(a * b for a, b in zip(vacancy_vector, resume_vector, strict=True))
    cosine = max(-1.0, min(1.0, cosine))
    latency_ms = (time.monotonic() - start) * 1000
    return ShadowScoreResult(
        e5_score=cosine,
        latency_ms=round(latency_ms, 1),
        used_fallback=False,
        vacancy_cache_hit=vacancy_hit,
        resume_cache_hit=resume_hit,
    )
