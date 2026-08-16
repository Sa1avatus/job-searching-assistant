"""Tests for matching cache and duration integration."""

import asyncio
from datetime import date

from app.matching.cache import (
    MatchingCache,
    build_decomposition_cache_key,
    build_entailment_cache_key,
)
from app.matching.claims import (
    AtomicClaim,
    ClaimType,
    Criticality,
    LogicalGroup,
    RequirementCriticality,
    RequirementDecomposition,
)
from app.matching.duration import ExperienceInterval, evaluate_duration
from app.matching.entailment import EntailmentRelation, EntailmentResult

# ── Helpers ──────────────────────────────────────────────────────


def _decomposition(requirement_id: str = "r1", text: str = "Python") -> RequirementDecomposition:
    return RequirementDecomposition(
        requirement_id=requirement_id,
        requirement_text=text,
        requirement_criticality=RequirementCriticality.REQUIRED,
        claims=[
            AtomicClaim(
                id=f"{requirement_id}:skill",
                requirement_id=requirement_id,
                claim_type=ClaimType.SKILL,
                subject=text,
                normalized_subject=text.lower(),
                criticality=Criticality.REQUIRED,
                logical_group=LogicalGroup.AND,
                source_text=text,
            )
        ],
        is_composite=False,
    )


def _entailment(claim_id: str = "c1", evidence_id: str = "e1") -> EntailmentResult:
    return EntailmentResult(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation=EntailmentRelation.ENTAILED,
        confidence=0.9,
    )


# ── Cache Tests ──────────────────────────────────────────────────


def test_cache_decomposition_roundtrip():
    async def run() -> None:
        cache = MatchingCache()
        key = build_decomposition_cache_key(
            vacancy_id="v1",
            requirement_text="Python",
            model_name="test",
            prompt_version="1",
        )
        assert await cache.get_decomposition(key) is None
        value = _decomposition()
        await cache.set_decomposition(key, value)
        result = await cache.get_decomposition(key)
        assert result is not None
        assert result.requirement_id == value.requirement_id
        assert len(result.claims) == 1

    asyncio.run(run())


def test_cache_entailment_roundtrip():
    async def run() -> None:
        cache = MatchingCache()
        key = build_entailment_cache_key(
            claim_text="Python production",
            evidence_text="Built FastAPI",
            claim_type="production_experience",
            model_name="test",
            prompt_version="1",
        )
        assert await cache.get_entailment(key) is None
        value = _entailment()
        await cache.set_entailment(key, value)
        result = await cache.get_entailment(key)
        assert result is not None
        assert result.relation is EntailmentRelation.ENTAILED

    asyncio.run(run())


def test_cache_eviction():
    async def run() -> None:
        cache = MatchingCache(max_size=2)
        await cache.set_decomposition("k1", _decomposition("r1"))
        await cache.set_decomposition("k2", _decomposition("r2"))
        await cache.set_decomposition("k3", _decomposition("r3"))  # evicts k1
        assert await cache.get_decomposition("k1") is None
        assert await cache.get_decomposition("k2") is not None
        assert await cache.get_decomposition("k3") is not None

    asyncio.run(run())


def test_cache_size_and_clear():
    async def run() -> None:
        cache = MatchingCache()
        assert cache.size == 0
        await cache.set_decomposition("k1", _decomposition("r1"))
        assert cache.size == 1
        await cache.set_entailment("k2", _entailment())
        assert cache.size == 2
        await cache.clear()
        assert cache.size == 0

    asyncio.run(run())


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


def test_entailment_key_includes_source_requirement():
    key1 = build_entailment_cache_key(
        claim_text="python skill",
        evidence_text="Built FastAPI",
        claim_type="skill",
        model_name="m",
        prompt_version="3",
        source_requirement="Python",
    )
    key2 = build_entailment_cache_key(
        claim_text="python skill",
        evidence_text="Built FastAPI",
        claim_type="skill",
        model_name="m",
        prompt_version="3",
        source_requirement="Python and SQL",
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
