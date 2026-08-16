"""Cache for expensive matching operations.

Caches decomposition and entailment results with content-based keys
that invalidate when vacancy, profile, model, or prompt changes.

Two backends:
- In-memory dict (default, bounded LRU-style) for tests and local single-run use.
- Redis (optional) for cross-run persistence: a smart recalculation then reuses prior
  decomposition and entailment results instead of re-invoking the LLM hundreds of times.
"""

from __future__ import annotations

import hashlib
from typing import TypeVar

import redis.asyncio as aioredis
import structlog

from app.matching.claims import RequirementDecomposition
from app.matching.entailment import EntailmentResult

logger = structlog.get_logger(__name__)

_T = TypeVar("_T")

_DECOMPOSITION_PREFIX = "jsa:match:dec:"
_ENTAILMENT_PREFIX = "jsa:match:ent:"


class MatchingCache:
    def __init__(
        self,
        *,
        redis: aioredis.Redis | None = None,
        ttl_seconds: int = 604_800,
        max_size: int = 1000,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size
        self._decomposition_cache: dict[str, RequirementDecomposition] = {}
        self._entailment_cache: dict[str, EntailmentResult] = {}

    # ── Decomposition ───────────────────────────────────────────

    async def get_decomposition(self, key: str) -> RequirementDecomposition | None:
        if self._redis is not None:
            raw = await self._redis.get(_DECOMPOSITION_PREFIX + key)
            if raw is None:
                return None
            try:
                return RequirementDecomposition.model_validate_json(raw)
            except Exception:
                logger.warning("matching_cache_decomposition_deserialize_failed")
                return None
        return self._decomposition_cache.get(key)

    async def set_decomposition(self, key: str, value: RequirementDecomposition) -> None:
        if self._redis is not None:
            await self._redis.set(
                _DECOMPOSITION_PREFIX + key,
                value.model_dump_json(),
                ex=self._ttl_seconds,
            )
            return
        self._evict(self._decomposition_cache)
        self._decomposition_cache[key] = value

    # ── Entailment ──────────────────────────────────────────────

    async def get_entailment(self, key: str) -> EntailmentResult | None:
        if self._redis is not None:
            raw = await self._redis.get(_ENTAILMENT_PREFIX + key)
            if raw is None:
                return None
            try:
                return EntailmentResult.model_validate_json(raw)
            except Exception:
                logger.warning("matching_cache_entailment_deserialize_failed")
                return None
        return self._entailment_cache.get(key)

    async def set_entailment(self, key: str, value: EntailmentResult) -> None:
        if self._redis is not None:
            await self._redis.set(
                _ENTAILMENT_PREFIX + key,
                value.model_dump_json(),
                ex=self._ttl_seconds,
            )
            return
        self._evict(self._entailment_cache)
        self._entailment_cache[key] = value

    # ── Invalidation ────────────────────────────────────────────

    async def invalidate_for_vacancy(self, vacancy_id: str) -> int:
        """Remove cached entries related to a vacancy (in-memory backend only)."""
        prefix = _hash_key(vacancy_id)[:8]
        removed = 0
        for cache in (self._decomposition_cache, self._entailment_cache):
            keys_to_remove = [k for k in cache if k.startswith(prefix)]
            for key in keys_to_remove:
                del cache[key]
                removed += 1
        return removed

    async def invalidate_for_profile(self, user_id: str) -> int:
        """Remove cached entries related to a profile (in-memory backend only)."""
        prefix = _hash_key(user_id)[:8]
        removed = 0
        for cache in (self._decomposition_cache, self._entailment_cache):
            keys_to_remove = [k for k in cache if prefix in k]
            for key in keys_to_remove:
                del cache[key]
                removed += 1
        return removed

    async def clear(self) -> None:
        self._decomposition_cache.clear()
        self._entailment_cache.clear()

    @property
    def size(self) -> int:
        return len(self._decomposition_cache) + len(self._entailment_cache)

    def _evict(self, cache: dict[str, _T]) -> None:
        if len(cache) >= self._max_size:
            oldest = next(iter(cache))
            del cache[oldest]


def build_decomposition_cache_key(
    *,
    vacancy_id: str,
    requirement_text: str,
    model_name: str,
    prompt_version: str,
    requirement_type: str = "",
    importance: str = "",
    is_blocker: str = "",
) -> str:
    """Build cache key for requirement decomposition.

    The key is content-based (requirement text + type + importance + blocker +
    model + prompt version) so identical requirements share decomposition results
    even when their database row ids change after a forced re-extraction. Pass an
    empty ``vacancy_id`` when the row id is ephemeral.
    """
    content = (
        f"{vacancy_id}:{requirement_text}:{model_name}:{prompt_version}:"
        f"{requirement_type}:{importance}:{is_blocker}"
    )
    return _hash_key(content)


def build_entailment_cache_key(
    *,
    claim_text: str,
    evidence_text: str,
    claim_type: str,
    model_name: str,
    prompt_version: str,
    source_requirement: str = "",
) -> str:
    """Build cache key for evidence entailment evaluation.

    ``source_requirement`` is included because it is part of the evaluator prompt;
    two claims sharing a subject but differing in source text must not collide.
    """
    content = (
        f"{claim_text}:{evidence_text}:{claim_type}:{model_name}:{prompt_version}:"
        f"{source_requirement}"
    )
    return _hash_key(content)


def _hash_key(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
