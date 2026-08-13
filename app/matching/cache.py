"""Cache for expensive matching operations.

Caches decomposition and entailment results with content-based keys
that invalidate when vacancy, profile, model, or prompt changes.
"""

from __future__ import annotations

import hashlib
from typing import TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class MatchingCache:
    """In-memory LRU-style cache for matching pipeline results.

    Keys combine: vacancy content hash, requirement text, claim text,
    profile facts hash, model name, prompt version.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._max_size = max_size
        self._decomposition_cache: dict[str, object] = {}
        self._entailment_cache: dict[str, object] = {}

    def get_decomposition(self, key: str) -> object | None:
        return self._decomposition_cache.get(key)

    def set_decomposition(self, key: str, value: object) -> None:
        if len(self._decomposition_cache) >= self._max_size:
            # Evict oldest (first inserted)
            oldest = next(iter(self._decomposition_cache))
            del self._decomposition_cache[oldest]
        self._decomposition_cache[key] = value

    def get_entailment(self, key: str) -> object | None:
        return self._entailment_cache.get(key)

    def set_entailment(self, key: str, value: object) -> None:
        if len(self._entailment_cache) >= self._max_size:
            oldest = next(iter(self._entailment_cache))
            del self._entailment_cache[oldest]
        self._entailment_cache[key] = value

    def invalidate_for_vacancy(self, vacancy_id: str) -> int:
        """Remove all cached entries related to a vacancy."""
        prefix = _hash_key(vacancy_id)[:8]
        removed = 0
        for cache in (self._decomposition_cache, self._entailment_cache):
            keys_to_remove = [k for k in cache if k.startswith(prefix)]
            for key in keys_to_remove:
                del cache[key]
                removed += 1
        return removed

    def invalidate_for_profile(self, user_id: str) -> int:
        """Remove all cached entries related to a profile."""
        prefix = _hash_key(user_id)[:8]
        removed = 0
        for cache in (self._decomposition_cache, self._entailment_cache):
            keys_to_remove = [k for k in cache if prefix in k]
            for key in keys_to_remove:
                del cache[key]
                removed += 1
        return removed

    def clear(self) -> None:
        self._decomposition_cache.clear()
        self._entailment_cache.clear()

    @property
    def size(self) -> int:
        return len(self._decomposition_cache) + len(self._entailment_cache)


def build_decomposition_cache_key(
    *,
    vacancy_id: str,
    requirement_text: str,
    model_name: str,
    prompt_version: str,
) -> str:
    """Build cache key for requirement decomposition."""
    content = f"{vacancy_id}:{requirement_text}:{model_name}:{prompt_version}"
    return _hash_key(content)


def build_entailment_cache_key(
    *,
    claim_text: str,
    evidence_text: str,
    claim_type: str,
    model_name: str,
    prompt_version: str,
) -> str:
    """Build cache key for evidence entailment evaluation."""
    content = f"{claim_text}:{evidence_text}:{claim_type}:{model_name}:{prompt_version}"
    return _hash_key(content)


def _hash_key(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
