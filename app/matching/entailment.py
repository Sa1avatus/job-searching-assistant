"""Evidence entailment evaluation.

Evaluates whether retrieved evidence actually supports an atomic claim,
as opposed to merely being semantically similar.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class EntailmentRelation(StrEnum):
    """Whether evidence entails the claim."""

    ENTAILED = "entailed"
    PARTIAL = "partial"
    RELATED_BUT_INSUFFICIENT = "related_but_insufficient"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class EvidenceStrengthCategory(StrEnum):
    NONE = "none"
    WEAK = "weak"
    PARTIAL = "partial"
    STRONG = "strong"
    EXPLICIT = "explicit"


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
    EntailmentRelation.CONTRADICTED: 0.0,
    EntailmentRelation.UNKNOWN: 0.0,
}


def compute_evidence_strength(
    relation: EntailmentRelation,
    semantic_score: float | None,
    reranker_score: float | None,
    entailment_score: float,
    *,
    weights: dict[str, float] | None = None,
) -> tuple[float, EvidenceStrengthCategory]:
    """Compute evidence strength from component scores.

    Returns (strength, category).
    """
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
    ) -> EntailmentResult: ...
