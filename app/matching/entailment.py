"""Evidence entailment evaluation.

Evaluates whether retrieved evidence actually supports an atomic claim,
as opposed to merely being semantically similar.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class EntailmentRelation(StrEnum):
    """Whether evidence entails the claim.

    ENTAILED: evidence explicitly/logically establishes the claim
    PARTIAL: evidence establishes part of the claim
    RELATED_BUT_INSUFFICIENT: evidence is topically related but does not establish the claim
    INSUFFICIENT_EVIDENCE: available data is insufficient to make a determination
        (e.g. dates missing for duration claim, no evidence retrieved)
    CONTRADICTED: explicit contradiction exists
    EVALUATION_ERROR: evaluator technical failure (timeout, invalid output, provider error)
    UNKNOWN: legacy fallback (should be phased out)
    """

    ENTAILED = "entailed"
    PARTIAL = "partial"
    RELATED_BUT_INSUFFICIENT = "related_but_insufficient"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONTRADICTED = "contradicted"
    EVALUATION_ERROR = "evaluation_error"
    UNKNOWN = "unknown"


class EvidenceStrengthCategory(StrEnum):
    NONE = "none"
    WEAK = "weak"
    PARTIAL = "partial"
    STRONG = "strong"
    EXPLICIT = "explicit"


class EvidenceType(StrEnum):
    """Type of evidence supporting a claim."""

    DIRECT = "direct"
    INDIRECT = "indirect"
    CONTEXTUAL = "contextual"
    NONE = "none"


class ClaimExperienceLevel(StrEnum):
    """Experience level detected in evidence for a claim."""

    COMMERCIAL_PRODUCTION = "commercial_production"
    INTERNAL_PRODUCTION = "internal_production"
    WORKING_PERSONAL_PROJECT = "working_personal_project"
    PROTOTYPE = "prototype"
    EXPERIMENT = "experiment"
    STUDIED_ONLY = "studied_only"
    NONE = "none"


# Configurable coefficients for evidence type and experience level
_EVIDENCE_TYPE_COEFFICIENTS: dict[EvidenceType, float] = {
    EvidenceType.DIRECT: 1.00,
    EvidenceType.INDIRECT: 0.70,
    EvidenceType.CONTEXTUAL: 0.40,
    EvidenceType.NONE: 0.00,
}

_EXPERIENCE_LEVEL_COEFFICIENTS: dict[ClaimExperienceLevel, float] = {
    ClaimExperienceLevel.COMMERCIAL_PRODUCTION: 1.00,
    ClaimExperienceLevel.INTERNAL_PRODUCTION: 0.95,
    ClaimExperienceLevel.WORKING_PERSONAL_PROJECT: 0.80,
    ClaimExperienceLevel.PROTOTYPE: 0.65,
    ClaimExperienceLevel.EXPERIMENT: 0.50,
    ClaimExperienceLevel.STUDIED_ONLY: 0.25,
    ClaimExperienceLevel.NONE: 0.00,
}


class StrictEntailmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EntailmentResult(StrictEntailmentModel):
    """Result of evaluating one evidence item against one claim."""

    claim_id: str
    evidence_id: str
    relation: EntailmentRelation
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=2_000)
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    reranker_score: float | None = Field(default=None, ge=0, le=1)
    entailment_score: float = Field(ge=0, le=1, default=0.0)
    evidence_strength: float = Field(ge=0, le=1, default=0.0)
    evidence_strength_category: EvidenceStrengthCategory = EvidenceStrengthCategory.NONE
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, object] = Field(default_factory=dict)
    # Coverage-based scoring: degree of claim fulfillment (0-1)
    coverage: float = Field(ge=0, le=1, default=0.0)
    # Type of evidence (direct, indirect, contextual, none)
    evidence_type: EvidenceType = EvidenceType.NONE
    # Experience level detected in evidence
    experience_level: ClaimExperienceLevel = ClaimExperienceLevel.NONE
    # New: structured error info for technical failures
    error_type: str | None = Field(default=None, max_length=100)
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    retry_count: int = Field(default=0, ge=0)


class EntailmentEvaluation(StrictEntailmentModel):
    """Aggregated entailment results for one claim across all evidence."""

    claim_id: str
    best_relation: EntailmentRelation
    best_evidence_strength: float = Field(ge=0, le=1)
    best_evidence_id: str | None = None
    evaluations: list[EntailmentResult] = Field(default_factory=list, max_length=20)
    aggregate_confidence: float = Field(ge=0, le=1, default=0.0)


_ENTAILMENT_WEIGHTS = {
    "semantic_score": 0.25,
    "reranker_score": 0.25,
    "entailment_score": 0.50,
}

_RELATION_STRENGTH_CAPS = {
    EntailmentRelation.ENTAILED: 1.0,
    EntailmentRelation.PARTIAL: 0.74,
    EntailmentRelation.RELATED_BUT_INSUFFICIENT: 0.49,
    EntailmentRelation.INSUFFICIENT_EVIDENCE: 0.0,
    EntailmentRelation.CONTRADICTED: 0.0,
    EntailmentRelation.EVALUATION_ERROR: 0.0,
    EntailmentRelation.UNKNOWN: 0.0,
}


def compute_evidence_strength(
    relation: EntailmentRelation,
    semantic_score: float | None,
    reranker_score: float | None,
    entailment_score: float,
    *,
    weights: dict[str, float] | None = None,
    coverage: float = 0.0,
    evidence_type: EvidenceType = EvidenceType.NONE,
    experience_level: ClaimExperienceLevel = ClaimExperienceLevel.NONE,
) -> tuple[float, EvidenceStrengthCategory]:
    """Compute evidence strength from component scores.

    When coverage > 0, uses coverage-based scoring:
        claim_score = coverage × evidence_quality_factor
    where evidence_quality_factor combines evidence_type and experience_level.

    Falls back to the legacy blend (semantic + reranker + entailment) when coverage is 0.
    Returns (strength, category).
    """
    if coverage > 0:
        # Coverage-based scoring (preferred)
        type_coeff = _EVIDENCE_TYPE_COEFFICIENTS.get(evidence_type, 0.0)
        exp_coeff = _EXPERIENCE_LEVEL_COEFFICIENTS.get(experience_level, 0.0)
        # evidence_quality_factor: blend of type and experience, bounded 0.7-1.0 for supported
        if type_coeff > 0 and exp_coeff > 0:
            quality_factor = max(0.7, min(1.0, 0.5 * type_coeff + 0.5 * exp_coeff))
        elif type_coeff > 0:
            quality_factor = max(0.5, min(0.85, type_coeff))
        else:
            quality_factor = 0.0
        strength = min(coverage * quality_factor, _RELATION_STRENGTH_CAPS.get(relation, 1.0))
    else:
        # Legacy blend
        w = weights or _ENTAILMENT_WEIGHTS
        normalized_semantic = semantic_score if semantic_score is not None else 0.0
        normalized_reranker = reranker_score if reranker_score is not None else 0.0

        raw = (
            w.get("semantic_score", 0.25) * normalized_semantic
            + w.get("reranker_score", 0.25) * normalized_reranker
            + w.get("entailment_score", 0.50) * entailment_score
        )

        cap = _RELATION_STRENGTH_CAPS.get(relation, 0.0)
        strength = min(raw, cap)

    if strength < 0.30:
        category = EvidenceStrengthCategory.NONE
    elif strength < 0.55:
        category = EvidenceStrengthCategory.WEAK
    elif strength < 0.75:
        category = EvidenceStrengthCategory.PARTIAL
    elif strength < 0.90:
        category = EvidenceStrengthCategory.STRONG
    else:
        category = EvidenceStrengthCategory.EXPLICIT

    return round(strength, 4), category


class EvidenceEvaluator(Protocol):
    """Protocol for evaluating evidence entailment against claims."""

    model_name: str
    model_version: str

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
        # New: structured claim context for better evaluation
        claim_subject: str | None = None,
        claim_criticality: str | None = None,
        source_requirement: str | None = None,
    ) -> EntailmentResult: ...
