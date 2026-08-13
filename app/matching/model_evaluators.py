"""LLM-backed claim decomposer and evidence evaluator.

Uses the existing ModelRouter to perform structured LLM calls
for requirement decomposition and evidence entailment evaluation.
Results are cached with content-based keys.
"""

from __future__ import annotations

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.matching.cache import (
    MatchingCache,
    build_decomposition_cache_key,
    build_entailment_cache_key,
)
from app.matching.claims import RequirementDecomposition
from app.matching.entailment import (
    EntailmentRelation,
    EntailmentResult,
    compute_evidence_strength,
)
from app.matching.extraction import StrictExtractionModel
from app.matching.normalization import SkillNormalizer
from app.prompts.registry import PromptRegistry

_PROMPT_VERSION_DECOMPOSE = "2"
_PROMPT_VERSION_ENTAIL = "2"

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
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._skill_normalizer = skill_normalizer or SkillNormalizer()
        self._cache = cache

    async def decompose(
        self,
        *,
        requirement_id: str,
        requirement_text: str,
        requirement_type: str,
        importance: str,
        is_blocker: bool,
    ) -> RequirementDecomposition:
        if self._cache is not None:
            cache_key = build_decomposition_cache_key(
                vacancy_id=requirement_id,
                requirement_text=requirement_text,
                model_name=self.model_name,
                prompt_version=_PROMPT_VERSION_DECOMPOSE,
            )
            cached = self._cache.get_decomposition(cache_key)
            if cached is not None and isinstance(cached, RequirementDecomposition):
                return cached

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
                timeout_seconds=60,
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

        if self._cache is not None:
            cache_key = build_decomposition_cache_key(
                vacancy_id=requirement_id,
                requirement_text=requirement_text,
                model_name=self.model_name,
                prompt_version=_PROMPT_VERSION_DECOMPOSE,
            )
            self._cache.set_decomposition(cache_key, result)

        return result


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
    ) -> None:
        self._router = router
        self._prompt_registry = prompt_registry
        self._cache = cache

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
        if self._cache is not None:
            cache_key = build_entailment_cache_key(
                claim_text=claim_text,
                evidence_text=evidence_text,
                claim_type=claim_type,
                model_name=self.model_name,
                prompt_version=_PROMPT_VERSION_ENTAIL,
            )
            cached = self._cache.get_entailment(cache_key)
            if cached is not None and isinstance(cached, EntailmentResult):
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
                    timeout_seconds=60,
                ),
                _RawEntailmentResult,
            )
        except Exception as error:
            error_type = type(error).__name__
            return self._build_error_result(
                claim_id, evidence_id, semantic_score, reranker_score,
                error_type=error_type,
                reason=f"Evaluator technical failure: {error_type}: {str(error)[:200]}",
            )

        # Parse relation — invalid LLM output → EVALUATION_ERROR, not UNKNOWN
        try:
            relation = EntailmentRelation(raw_result.relation)
        except ValueError:
            return self._build_error_result(
                claim_id, evidence_id, semantic_score, reranker_score,
                error_type="invalid_relation_value",
                reason=f"LLM returned unrecognized relation: {raw_result.relation!r}",
            )

        entailment_score = raw_result.entailment_score
        if entailment_score == 0.0:
            entailment_score = _RELATION_BASE_SCORE.get(relation, 0.0)

        strength, category = compute_evidence_strength(
            relation, semantic_score, reranker_score, entailment_score
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
        )

        if self._cache is not None:
            cache_key = build_entailment_cache_key(
                claim_text=claim_text,
                evidence_text=evidence_text,
                claim_type=claim_type,
                model_name=self.model_name,
                prompt_version=_PROMPT_VERSION_ENTAIL,
            )
            self._cache.set_entailment(cache_key, result)

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
            error_type=error_type,
        )


class _RawEntailmentResult(StrictExtractionModel):
    """Internal schema for LLM entailment evaluation output."""

    relation: str
    confidence: float = 0.5
    reason: str = ""
    entailment_score: float = 0.0
