"""LLM-backed claim decomposer and evidence evaluator.

Uses the existing ModelRouter to perform structured LLM calls
for requirement decomposition and evidence entailment evaluation.
Results are cached with content-based keys (including the resolved model
identity), so a smart recalculation reuses prior LLM work across runs.
"""

from __future__ import annotations

import re

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
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
from app.matching.entailment import (
    ClaimExperienceLevel,
    EntailmentRelation,
    EntailmentResult,
    EvidenceType,
    compute_evidence_strength,
)
from app.matching.extraction import StrictExtractionModel
from app.matching.normalization import SkillNormalizer
from app.prompts.registry import PromptRegistry

_PROMPT_VERSION_DECOMPOSE = "2"
_PROMPT_VERSION_ENTAIL = "3"

# Base entailment scores when LLM doesn't provide one
_RELATION_BASE_SCORE = {
    EntailmentRelation.ENTAILED: 0.95,
    EntailmentRelation.PARTIAL: 0.6,
    EntailmentRelation.RELATED_BUT_INSUFFICIENT: 0.3,
    EntailmentRelation.INSUFFICIENT_EVIDENCE: 0.0,
    EntailmentRelation.CONTRADICTED: 0.0,
    EntailmentRelation.EVALUATION_ERROR: 0.0,
    EntailmentRelation.UNKNOWN: 0.0,
}

# Deterministic decomposition of obvious single-skill requirements. A single-skill
# requirement (e.g. "PostgreSQL", "Python", "Docker") does not need an LLM decomposition
# call — it is already a single atomic claim. Composite requirements (conjunctions,
# quantifiers, durations, "knowledge of X and Y") still go through the LLM.
_SIMPLE_SKILL_REQUIREMENT_TYPES = frozenset({"hard_skill", "technology", "domain"})
_MAX_SIMPLE_SKILL_WORDS = 4
_MAX_SIMPLE_SKILL_LENGTH = 80
_COMPOSITE_MARKER = re.compile(
    r"[,;/()]|\b(and|or|или|plus|experience|опыт|опыта|years|лет|год|минимум|minimum|"
    r"at least|knowledge|знание|знания|уровень|level)\b",
    re.IGNORECASE,
)


def _criticality_for(importance: str, is_blocker: bool) -> Criticality:
    if is_blocker:
        return Criticality.HARD_BLOCKER
    if importance == "required":
        return Criticality.REQUIRED
    return Criticality.PREFERRED


def _requirement_criticality_for(importance: str, is_blocker: bool) -> RequirementCriticality:
    if is_blocker:
        return RequirementCriticality.HARD_BLOCKER
    if importance == "required":
        return RequirementCriticality.REQUIRED
    return RequirementCriticality.PREFERRED


class RouterRequirementDecomposer:
    """Decomposes vacancy requirements into atomic claims via LLM."""

    model_name = "model-router"
    model_version = "provider-selected"
    schema_version = "2"

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        skill_normalizer: SkillNormalizer | None = None,
        cache: MatchingCache | None = None,
        timeout_seconds: float = 60,
        model_identity: str | None = None,
        decompose_max_tokens: int = 2048,
        decompose_context_size: int = 4096,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._skill_normalizer = skill_normalizer or SkillNormalizer()
        self._cache = cache
        self._timeout_seconds = timeout_seconds
        self._model_identity = model_identity or self.model_name
        self._decompose_max_tokens = decompose_max_tokens
        self._decompose_context_size = decompose_context_size

    async def decompose(
        self,
        *,
        requirement_id: str,
        requirement_text: str,
        requirement_type: str,
        importance: str,
        is_blocker: bool,
    ) -> RequirementDecomposition:
        deterministic = self._deterministic_single_skill_claim(
            requirement_id,
            requirement_text,
            requirement_type,
            importance,
            is_blocker,
        )
        if deterministic is not None:
            return deterministic

        cache_key: str | None = None
        if self._cache is not None:
            cache_key = build_decomposition_cache_key(
                vacancy_id="",
                requirement_text=requirement_text,
                model_name=self._model_identity,
                prompt_version=_PROMPT_VERSION_DECOMPOSE,
                requirement_type=requirement_type,
                importance=importance,
                is_blocker=str(is_blocker),
            )
            cached = await self._cache.get_decomposition(cache_key)
            if cached is not None:
                return self._remap_decomposition(cached, requirement_id)

        prompt = self._prompt_registry.render(
            "decompose_requirement",
            {
                "requirement_id": requirement_id,
                "requirement_text": requirement_text,
                "requirement_type": requirement_type,
                "importance": importance,
                "is_blocker": str(is_blocker),
            },
        )
        result = await self._router.route(
            ModelRequest(
                task_name="decompose_requirement",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=self._timeout_seconds,
                max_output_tokens=self._decompose_max_tokens,
                context_size=self._decompose_context_size,
            ),
            RequirementDecomposition,
        )
        normalized_claims = []
        for claim in result.claims:
            normalized = self._skill_normalizer.normalize(claim.subject)
            normalized_claims.append(
                claim.model_copy(update={"normalized_subject": normalized.canonical})
            )
        result = result.model_copy(update={"claims": normalized_claims})

        if self._cache is not None and cache_key is not None:
            await self._cache.set_decomposition(cache_key, result)

        return result

    def _deterministic_single_skill_claim(
        self,
        requirement_id: str,
        requirement_text: str,
        requirement_type: str,
        importance: str,
        is_blocker: bool,
    ) -> RequirementDecomposition | None:
        if requirement_type not in _SIMPLE_SKILL_REQUIREMENT_TYPES:
            return None
        text = " ".join(requirement_text.split())
        if not text or len(text) > _MAX_SIMPLE_SKILL_LENGTH:
            return None
        if len(text.split()) > _MAX_SIMPLE_SKILL_WORDS:
            return None
        if _COMPOSITE_MARKER.search(text):
            return None
        normalized = self._skill_normalizer.normalize(text).canonical
        claim = AtomicClaim(
            id=f"{requirement_id}:skill",
            requirement_id=requirement_id,
            claim_type=ClaimType.SKILL,
            subject=text,
            normalized_subject=normalized,
            criticality=_criticality_for(importance, is_blocker),
            logical_group=LogicalGroup.AND,
            source_text=text,
        )
        return RequirementDecomposition(
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            requirement_criticality=_requirement_criticality_for(importance, is_blocker),
            claims=[claim],
            is_composite=False,
            decomposition_confidence=1.0,
        )

    @staticmethod
    def _remap_decomposition(
        decomposition: RequirementDecomposition,
        requirement_id: str,
    ) -> RequirementDecomposition:
        """Rebind a cached decomposition to the current requirement row id.

        Claim ids generated by the LLM are prefixed with the requirement id, so the
        remap rewrites both the decomposition owner and every claim id/owner.
        """
        if decomposition.requirement_id == requirement_id:
            return decomposition
        old_id = decomposition.requirement_id
        remapped_claims = []
        for claim in decomposition.claims:
            claim_id = claim.id
            if old_id and claim_id.startswith(old_id):
                claim_id = requirement_id + claim_id[len(old_id) :]
            remapped_claims.append(
                claim.model_copy(update={"id": claim_id, "requirement_id": requirement_id})
            )
        return decomposition.model_copy(
            update={"requirement_id": requirement_id, "claims": remapped_claims}
        )


class RouterEvidenceEvaluator:
    """Evaluates evidence entailment against claims via LLM.

    Key design: technical failures (timeout, invalid JSON, provider error)
    are reported as EVALUATION_ERROR, never as UNKNOWN or MISSING.
    """

    model_name = "model-router"
    model_version = "provider-selected"

    def __init__(
        self,
        router: ModelRouter,
        prompt_registry: PromptRegistry,
        *,
        cache: MatchingCache | None = None,
        timeout_seconds: float = 60,
        model_identity: str | None = None,
        entailment_max_tokens: int = 512,
        entailment_context_size: int = 4096,
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._cache = cache
        self._timeout_seconds = timeout_seconds
        self._model_identity = model_identity or self.model_name
        self._entailment_max_tokens = entailment_max_tokens
        self._entailment_context_size = entailment_context_size

    async def evaluate(
        self,
        *,
        claim_id: str,
        claim_text: str,
        claim_type: str,
        evidence_id: str,
        evidence_text: str,
        semantic_score: float | None = None,
        reranker_score: float | None = None,
        claim_subject: str | None = None,
        claim_criticality: str | None = None,
        source_requirement: str | None = None,
    ) -> EntailmentResult:
        cache_key: str | None = None
        if self._cache is not None:
            cache_key = build_entailment_cache_key(
                claim_text=claim_text,
                evidence_text=evidence_text,
                claim_type=claim_type,
                model_name=self._model_identity,
                prompt_version=_PROMPT_VERSION_ENTAIL,
                source_requirement=source_requirement or "",
            )
            cached = await self._cache.get_entailment(cache_key)
            if cached is not None:
                return cached.model_copy(
                    update={
                        "claim_id": claim_id,
                        "evidence_id": evidence_id,
                        "semantic_score": semantic_score,
                        "reranker_score": reranker_score,
                    }
                )

        # Build structured claim context for the evaluator
        claim_context_parts = [f"Claim: {claim_text}", f"Type: {claim_type}"]
        if claim_subject:
            claim_context_parts.append(f"Subject: {claim_subject}")
        if claim_criticality:
            claim_context_parts.append(f"Criticality: {claim_criticality}")
        if source_requirement:
            claim_context_parts.append(f"Source requirement: {source_requirement}")
        claim_context = "\n".join(claim_context_parts)

        prompt = self._prompt_registry.render(
            "evaluate_evidence_entailment",
            {
                "claim_id": claim_id,
                "claim_text": claim_context,
                "claim_type": claim_type,
                "evidence_id": evidence_id,
                "evidence_text": evidence_text,
            },
        )

        # Technical failures → EVALUATION_ERROR, never UNKNOWN
        try:
            raw_result = await self._router.route(
                ModelRequest(
                    task_name="evaluate_evidence_entailment",
                    task_class=ModelTaskClass.LOW_COST,
                    prompt=prompt,
                    max_cost_usd=0.03,
                    timeout_seconds=self._timeout_seconds,
                    max_output_tokens=self._entailment_max_tokens,
                    context_size=self._entailment_context_size,
                ),
                _RawEntailmentResult,
            )
        except Exception as error:
            error_type = type(error).__name__
            return self._build_error_result(
                claim_id,
                evidence_id,
                semantic_score,
                reranker_score,
                error_type=error_type,
                reason=f"Evaluator technical failure ({error_type})",
            )

        # Parse relation — invalid LLM output → EVALUATION_ERROR, not UNKNOWN
        try:
            relation = EntailmentRelation(raw_result.relation)
        except ValueError:
            return self._build_error_result(
                claim_id,
                evidence_id,
                semantic_score,
                reranker_score,
                error_type="invalid_relation_value",
                reason="Evaluator returned an unsupported relation",
            )

        entailment_score = raw_result.entailment_score
        if entailment_score == 0.0:
            entailment_score = _RELATION_BASE_SCORE.get(relation, 0.0)

        # Parse evidence_type and experience_level with fallback
        try:
            evidence_type = EvidenceType(raw_result.evidence_type)
        except ValueError:
            evidence_type = EvidenceType.NONE
        try:
            experience_level = ClaimExperienceLevel(raw_result.experience_level)
        except ValueError:
            experience_level = ClaimExperienceLevel.NONE

        coverage = raw_result.coverage
        # If evaluator didn't provide coverage, derive from relation + entailment_score
        if coverage == 0.0 and relation in (
            EntailmentRelation.ENTAILED,
            EntailmentRelation.PARTIAL,
        ):
            coverage = (
                entailment_score
                if entailment_score > 0
                else _RELATION_BASE_SCORE.get(relation, 0.0)
            )

        strength, category = compute_evidence_strength(
            relation,
            semantic_score,
            reranker_score,
            entailment_score,
            coverage=coverage,
            evidence_type=evidence_type,
            experience_level=experience_level,
        )

        result = EntailmentResult(
            claim_id=claim_id,
            evidence_id=evidence_id,
            relation=relation,
            confidence=raw_result.confidence,
            reason=raw_result.reason,
            semantic_score=semantic_score,
            reranker_score=reranker_score,
            entailment_score=round(entailment_score, 4),
            evidence_strength=strength,
            evidence_strength_category=category,
            coverage=round(coverage, 4),
            evidence_type=evidence_type,
            experience_level=experience_level,
        )

        if self._cache is not None and cache_key is not None:
            await self._cache.set_entailment(cache_key, result)

        return result

    @staticmethod
    def _build_error_result(
        claim_id: str,
        evidence_id: str,
        semantic_score: float | None,
        reranker_score: float | None,
        *,
        error_type: str,
        reason: str,
    ) -> EntailmentResult:
        """Build an EntailmentResult for a technical evaluation failure."""
        strength, category = compute_evidence_strength(
            EntailmentRelation.EVALUATION_ERROR,
            semantic_score,
            reranker_score,
            0.0,
        )
        return EntailmentResult(
            claim_id=claim_id,
            evidence_id=evidence_id,
            relation=EntailmentRelation.EVALUATION_ERROR,
            confidence=0.0,
            reason=reason,
            semantic_score=semantic_score,
            reranker_score=reranker_score,
            entailment_score=0.0,
            evidence_strength=strength,
            evidence_strength_category=category,
            coverage=0.0,
            evidence_type=EvidenceType.NONE,
            experience_level=ClaimExperienceLevel.NONE,
            error_type=error_type,
        )


class _RawEntailmentResult(StrictExtractionModel):
    """Internal schema for LLM entailment evaluation output."""

    relation: str
    confidence: float = 0.5
    reason: str = ""
    entailment_score: float = 0.0
    coverage: float = 0.0
    evidence_type: str = "none"
    experience_level: str = "none"
