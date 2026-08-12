"""Claim-based matching pipeline.

Orchestrates requirement decomposition → per-claim retrieval →
reranking → entailment evaluation → deterministic aggregation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session

from app.matching.claims import (
    AtomicClaim,
    RequirementDecomposer,
    RequirementDecomposition,
)
from app.matching.entailment import (
    EntailmentRelation,
    EntailmentResult,
    EvidenceEvaluator,
)
from app.matching.extraction import RequirementImportance, RequirementType
from app.matching.gap_analysis import GapAnalysisResult, analyze_gaps
from app.matching.scoring import MatchLevel, RequirementAssessment
from app.matching.semantic import RerankedCandidate, Reranker
from app.observability.metrics import metrics
from app.storage.tables import CandidateEvidenceRow

logger = structlog.get_logger(__name__)


@dataclass
class ClaimMatchResult:
    """Result of evaluating a single claim against evidence."""

    claim: AtomicClaim
    best_entailment: EntailmentResult | None = None
    relation: str = "unknown"
    evidence_strength: float = 0.0
    has_evidence: bool = False
    duration_result: dict[str, object] | None = None


@dataclass
class RequirementClaimResults:
    """Aggregated results for all claims of one requirement."""

    requirement_id: str
    requirement_text: str
    claim_results: list[ClaimMatchResult] = field(default_factory=list)
    overall_relation: str = "unknown"
    overall_strength: float = 0.0
    match_level: MatchLevel = MatchLevel.MISSING
    is_hard_blocker: bool = False
    explanation: str = ""


@dataclass
class ClaimPipelineResult:
    """Full result of claim-based matching for all requirements."""

    assessments: list[RequirementAssessment] = field(default_factory=list)
    requirement_results: list[RequirementClaimResults] = field(default_factory=list)
    gap_analysis: GapAnalysisResult | None = None


class ClaimMatchPipeline:
    """Pipeline that decomposes requirements into claims and evaluates them."""

    def __init__(
        self,
        session: Session,
        decomposer: RequirementDecomposer,
        evaluator: EvidenceEvaluator,
        retriever: object,  # RequirementRetriever protocol
        reranker: Reranker,
        *,
        retrieval_top_k: int = 20,
        reranker_top_k: int = 5,
        fallback_enabled: bool = True,
    ) -> None:
        self._session = session
        self._decomposer = decomposer
        self._evaluator = evaluator
        self._retriever = retriever
        self._reranker = reranker
        self._retrieval_top_k = retrieval_top_k
        self._reranker_top_k = reranker_top_k
        self._fallback_enabled = fallback_enabled

    async def match_requirements(
        self,
        application_id: str,
        user_id: str,
        cv_file_id: str,
        requirements: tuple,
    ) -> ClaimPipelineResult:
        """Run claim-based matching for all requirements."""
        assessments = []
        requirement_results = []
        gap_inputs = []

        for requirement in requirements:
            try:
                req_result = await self._match_single_requirement(user_id, cv_file_id, requirement)
                requirement_results.append(req_result)

                assessment = self._build_assessment(requirement, req_result)
                assessments.append(assessment)

                # Collect gap analysis inputs
                for cr in req_result.claim_results:
                    gap_inputs.append(
                        {
                            "claim_id": cr.claim.id,
                            "claim_subject": cr.claim.subject,
                            "claim_type": cr.claim.claim_type.value,
                            "relation": cr.relation,
                            "evidence_strength": cr.evidence_strength,
                            "has_evidence": cr.has_evidence,
                            "duration_result": cr.duration_result,
                        }
                    )

            except Exception as error:
                logger.warning(
                    "claim_matching_failed_for_requirement",
                    requirement_id=requirement.id,
                    error_type=type(error).__name__,
                )
                metrics.increment("claim_matching_requirement_failures")
                assessments.append(
                    RequirementAssessment(
                        requirement_id=requirement.id,
                        requirement_type=RequirementType(requirement.requirement_type),
                        importance=RequirementImportance(requirement.importance),
                        weight=requirement.weight,
                        is_blocker=requirement.is_blocker,
                        match_level=MatchLevel.MISSING,
                    )
                )

        # Run gap analysis
        gap_analysis = analyze_gaps(gap_inputs) if gap_inputs else None

        return ClaimPipelineResult(
            assessments=assessments,
            requirement_results=requirement_results,
            gap_analysis=gap_analysis,
        )

    async def _match_single_requirement(
        self,
        user_id: str,
        cv_file_id: str,
        requirement,
    ) -> RequirementClaimResults:
        """Match a single requirement through claim decomposition."""
        decompose_started = time.perf_counter()
        decomposition = await self._decomposer.decompose(
            requirement_id=requirement.id,
            requirement_text=requirement.requirement_text,
            requirement_type=requirement.requirement_type,
            importance=requirement.importance,
            is_blocker=requirement.is_blocker,
        )
        decompose_duration = time.perf_counter() - decompose_started
        metrics.observe("claim_decomposition_duration_seconds", decompose_duration)
        metrics.set_gauge("claims_per_requirement", float(len(decomposition.claims)))

        if not decomposition.claims:
            return RequirementClaimResults(
                requirement_id=requirement.id,
                requirement_text=requirement.requirement_text,
                overall_relation="unknown",
                match_level=MatchLevel.MISSING,
            )

        # Evaluate each claim
        claim_results = []
        for claim in decomposition.claims:
            try:
                cr = await self._evaluate_single_claim(user_id, cv_file_id, claim)
                claim_results.append(cr)
            except Exception as error:
                logger.warning(
                    "claim_evaluation_failed",
                    claim_id=claim.id,
                    error_type=type(error).__name__,
                )
                claim_results.append(
                    ClaimMatchResult(
                        claim=claim,
                        relation="unknown",
                        evidence_strength=0.0,
                        has_evidence=False,
                    )
                )

        # Aggregate claim results
        return self._aggregate_claims(requirement, decomposition, claim_results)

    async def _evaluate_single_claim(
        self,
        user_id: str,
        cv_file_id: str,
        claim: AtomicClaim,
    ) -> ClaimMatchResult:
        """Retrieve evidence and evaluate entailment for one claim."""
        claim_text = f"{claim.normalized_subject} {claim.claim_type.value}".strip()

        # Targeted retrieval
        retrieval_started = time.perf_counter()
        candidates = await self._retriever.retrieve(
            claim_text,
            user_id=user_id,
            cv_file_id=cv_file_id,
        )
        metrics.observe("claim_retrieval_duration_seconds", time.perf_counter() - retrieval_started)

        # Rerank
        reranker_available = True
        try:
            reranked = await self._reranker.rerank(claim_text, candidates)
        except Exception:
            if not self._fallback_enabled:
                raise
            reranker_available = False
            metrics.increment("reranker_unavailable_total")
            reranked = tuple(
                RerankedCandidate(
                    candidate=c,
                    raw_score=c.hybrid_score,
                    normalized_score=c.hybrid_score,
                )
                for c in sorted(
                    candidates, key=lambda x: (x.hybrid_score, x.evidence_id), reverse=True
                )
            )

        # Evaluate entailment for top-N
        top_candidates = reranked[: self._reranker_top_k]
        best_entailment: EntailmentResult | None = None

        for candidate in top_candidates:
            evidence = self._session.get(CandidateEvidenceRow, candidate.candidate.evidence_id)
            if evidence is None:
                continue

            try:
                eval_result = await self._evaluator.evaluate(
                    claim_id=claim.id,
                    claim_text=claim_text,
                    claim_type=claim.claim_type.value,
                    evidence_id=evidence.id,
                    evidence_text=evidence.evidence_text,
                    semantic_score=candidate.candidate.hybrid_score,
                    reranker_score=(candidate.normalized_score if reranker_available else None),
                )
                if (
                    best_entailment is None
                    or eval_result.evidence_strength > best_entailment.evidence_strength
                ):
                    best_entailment = eval_result
            except Exception as error:
                logger.warning(
                    "entailment_evaluation_failed",
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    error_type=type(error).__name__,
                )

        return ClaimMatchResult(
            claim=claim,
            best_entailment=best_entailment,
            relation=best_entailment.relation.value if best_entailment else "unknown",
            evidence_strength=best_entailment.evidence_strength if best_entailment else 0.0,
            has_evidence=best_entailment is not None,
        )

    def _aggregate_claims(
        self,
        requirement,
        decomposition: RequirementDecomposition,
        claim_results: list[ClaimMatchResult],
    ) -> RequirementClaimResults:
        """Aggregate claim results into a requirement-level assessment."""
        if not claim_results:
            return RequirementClaimResults(
                requirement_id=requirement.id,
                requirement_text=requirement.requirement_text,
                match_level=MatchLevel.MISSING,
            )

        # Determine overall relation based on AND semantics
        required_claims = [
            cr for cr in claim_results if cr.claim.criticality.value in ("required", "hard_blocker")
        ]
        if not required_claims:
            required_claims = claim_results

        relations = [cr.relation for cr in required_claims]
        strengths = [cr.evidence_strength for cr in claim_results]

        if all(r == "entailed" for r in relations):
            overall_relation = "entailed"
        elif any(r == "entailed" for r in relations) or any(r == "partial" for r in relations):
            overall_relation = "partial"
        elif any(r == "related_but_insufficient" for r in relations):
            overall_relation = "related_but_insufficient"
        else:
            overall_relation = "unknown"

        # Map to match level
        _RELATION_TO_LEVEL = {
            "entailed": MatchLevel.EXACT,
            "partial": MatchLevel.PARTIAL,
            "related_but_insufficient": MatchLevel.RELATED,
            "unknown": MatchLevel.MISSING,
            "contradicted": MatchLevel.MISSING,
        }
        match_level = _RELATION_TO_LEVEL.get(overall_relation, MatchLevel.MISSING)

        is_hard_blocker = any(cr.claim.criticality.value == "hard_blocker" for cr in claim_results)

        # Build explanation
        lines = [f"Decomposed into {len(decomposition.claims)} claims:"]
        for cr in claim_results:
            symbol = {
                "entailed": "✓",
                "partial": "~",
                "related_but_insufficient": "?",
                "unknown": "✗",
                "contradicted": "✗",
            }.get(cr.relation, "?")
            lines.append(
                f"  {symbol} {cr.claim.subject}: {cr.relation} "
                f"(strength={cr.evidence_strength:.2f})"
            )

        return RequirementClaimResults(
            requirement_id=requirement.id,
            requirement_text=requirement.requirement_text,
            claim_results=claim_results,
            overall_relation=overall_relation,
            overall_strength=max(strengths) if strengths else 0.0,
            match_level=match_level,
            is_hard_blocker=is_hard_blocker,
            explanation="\n".join(lines),
        )

    def _build_assessment(
        self,
        requirement,
        req_result: RequirementClaimResults,
    ) -> RequirementAssessment:
        """Build a RequirementAssessment from claim results."""
        entailment_relation = None
        entailment_values = set(EntailmentRelation.__members__.values())
        if (
            req_result.overall_relation in entailment_values
            or req_result.overall_relation in EntailmentRelation
        ):
            entailment_relation = EntailmentRelation(req_result.overall_relation)

        experience_level = None
        best_entailment = None
        for cr in req_result.claim_results:
            if cr.best_entailment is not None and (
                best_entailment is None or cr.evidence_strength > best_entailment.evidence_strength
            ):
                best_entailment = cr.best_entailment

        return RequirementAssessment(
            requirement_id=requirement.id,
            requirement_type=RequirementType(requirement.requirement_type),
            importance=RequirementImportance(requirement.importance),
            weight=requirement.weight,
            is_blocker=requirement.is_blocker,
            is_hard_blocker=req_result.is_hard_blocker,
            match_level=req_result.match_level,
            entailment_relation=entailment_relation,
            evidence_strength=req_result.overall_strength,
            evidence_experience_level=experience_level,
        )
