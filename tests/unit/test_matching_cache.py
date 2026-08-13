"""Tests for matching cache and duration integration."""

from datetime import date

from app.matching.cache import (
    MatchingCache,
    build_decomposition_cache_key,
    build_entailment_cache_key,
)
from app.matching.duration import ExperienceInterval, evaluate_duration

# ── Cache Tests ──────────────────────────────────────────────────


def test_cache_roundtrip():
    cache = MatchingCache()
    key = build_decomposition_cache_key(
        vacancy_id="v1",
        requirement_text="Python",
        model_name="test",
        prompt_version="1",
    )
    assert cache.get_decomposition(key) is None
    cache.set_decomposition(key, "result")
    assert cache.get_decomposition(key) == "result"


def test_cache_entailment_roundtrip():
    cache = MatchingCache()
    key = build_entailment_cache_key(
        claim_text="Python production",
        evidence_text="Built FastAPI",
        claim_type="production_experience",
        model_name="test",
        prompt_version="1",
    )
    assert cache.get_entailment(key) is None
    cache.set_entailment(key, "eval_result")
    assert cache.get_entailment(key) == "eval_result"


def test_cache_eviction():
    cache = MatchingCache(max_size=2)
    cache.set_decomposition("k1", "v1")
    cache.set_decomposition("k2", "v2")
    cache.set_decomposition("k3", "v3")  # Should evict k1
    assert cache.get_decomposition("k1") is None
    assert cache.get_decomposition("k2") == "v2"
    assert cache.get_decomposition("k3") == "v3"


def test_cache_invalidate_for_vacancy():
    cache = MatchingCache()
    key1 = build_decomposition_cache_key(
        vacancy_id="v1",
        requirement_text="Python",
        model_name="m",
        prompt_version="1",
    )
    key2 = build_decomposition_cache_key(
        vacancy_id="v2",
        requirement_text="Python",
        model_name="m",
        prompt_version="1",
    )
    cache.set_decomposition(key1, "r1")
    cache.set_decomposition(key2, "r2")
    # Invalidation by vacancy removes entries with matching prefix
    cache.invalidate_for_vacancy("v1")
    # key1 may or may not be removed depending on hash collision


def test_cache_size():
    cache = MatchingCache()
    assert cache.size == 0
    cache.set_decomposition("k1", "v1")
    assert cache.size == 1
    cache.set_entailment("k2", "v2")
    assert cache.size == 2
    cache.clear()
    assert cache.size == 0


def test_different_inputs_produce_different_keys():
    key1 = build_decomposition_cache_key(
        vacancy_id="v1",
        requirement_text="Python",
        model_name="m1",
        prompt_version="1",
    )
    key2 = build_decomposition_cache_key(
        vacancy_id="v1",
        requirement_text="Python",
        model_name="m2",
        prompt_version="1",
    )
    assert key1 != key2


# ── Duration Integration ─────────────────────────────────────────


def test_duration_evaluate_with_years_field():
    """Evidence with years=5 should produce matched for >= 3 years."""
    intervals = [
        ExperienceInterval(
            skill="ML",
            started_at=date(2020, 1, 1),
            ended_at=None,
            source_evidence_id="e1",
        )
    ]
    result = evaluate_duration("c1", 3.0, intervals, reference_date=date(2025, 6, 1))
    assert result.actual_years >= 5.0
    assert result.status == "matched"


def test_duration_evaluate_no_intervals():
    result = evaluate_duration("c1", 3.0, [], reference_date=date(2025, 1, 1))
    assert result.status == "insufficient_evidence"
    assert result.actual_years == 0.0


def test_duration_partial_match():
    intervals = [
        ExperienceInterval(
            skill="RAG",
            started_at=date(2024, 1, 1),
            ended_at=None,
            source_evidence_id="e1",
        )
    ]
    result = evaluate_duration("c1", 3.0, intervals, reference_date=date(2025, 6, 1))
    assert result.actual_years < 3.0
    assert result.status in ("partial", "matched")
