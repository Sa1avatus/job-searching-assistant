"""Claim-based matching pipeline.

Orchestrates requirement decomposition → per-claim retrieval →
reranking → entailment evaluation → deterministic aggregation.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import structlog
from sqlalchemy.orm import Session

from app.matching.claims import (
    AtomicClaim,
    ClaimType,
    RequirementDecomposer,
    RequirementDecomposition,
)
from app.matching.duration import ExperienceInterval, evaluate_duration
from app.matching.entailment import (
    EntailmentRelation,
    EntailmentResult,
    EvidenceEvaluator,
)
from app.matching.extraction import RequirementImportance, RequirementType
from app.matching.gap_analysis import GapAnalysisResult, analyze_gaps
from app.matching.scoring import MatchLevel, RequirementAssessment
from app.matching.semantic import RerankedCandidate, Reranker, RetrievalCandidate
from app.observability.metrics import metrics
from app.storage.tables import CandidateEvidenceRow, VacancyRequirementRow

logger = structlog.get_logger(__name__)

# A claim is considered conclusively confirmed when the best candidate is directly
# entailed at this coverage, so the entailment loop can stop early instead of
# evaluating every retrieved candidate.
_EARLY_EXIT_COVERAGE = 0.8


class ClaimRetriever(Protocol):
    async def retrieve(
        self,
        requirement_text: str,
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[RetrievalCandidate, ...]: ...


# Symbols for UI display
_RELATION_SYMBOLS: dict[str, str] = {
    "entailed": "✓",
    "partial": "~",
    "related_but_insufficient": "?",
    "insufficient_evidence": "…",
    "evaluation_error": "⚠",
    "contradicted": "✗",
    "unknown": "✗",
    "missing": "✗",
}

_RELATION_LABELS_RU: dict[str, str] = {
    "entailed": "подтверждено",
    "partial": "частично подтверждено",
    "related_but_insufficient": "недостаточно данных",
    "insufficient_evidence": "недостаточно доказательств",
    "evaluation_error": "ошибка оценки",
    "contradicted": "противоречит",
    "unknown": "неизвестно",
    "missing": "отсутствует",
}

_RELATION_LABELS_EN: dict[str, str] = {
    "entailed": "confirmed",
    "partial": "partially confirmed",
    "related_but_insufficient": "insufficient evidence",
    "insufficient_evidence": "insufficient evidence",
    "evaluation_error": "evaluation error",
    "contradicted": "contradicted",
    "unknown": "unknown",
    "missing": "missing",
}


def _is_cyrillic(text: str) -> bool:
    """Check if text contains significant Cyrillic content."""
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    return cyrillic > len(text) * 0.1


@dataclass
class SynthesizedEvidence:
    """Aggregated evidence from multiple sources for one claim."""

    evidence_ids: list[str] = field(default_factory=list)
    combined_text: str = ""
    best_entailment: EntailmentResult | None = None
    contributing_entailments: list[EntailmentResult] = field(default_factory=list)


@dataclass
class ClaimMatchResult:
    """Result of evaluating a single claim against evidence."""

    claim: AtomicClaim
    best_entailment: EntailmentResult | None = None
    relation: str = "unknown"
    evidence_strength: float = 0.0
    has_evidence: bool = False
    duration_result: dict[str, object] | None = None
    synthesized: SynthesizedEvidence | None = None


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
        retriever: ClaimRetriever,
        reranker: Reranker,
        *,
        retrieval_top_k: int = 20,
        reranker_top_k: int = 5,
        fallback_enabled: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
        llm_concurrency: int = 10,
        entailment_max_candidates: int = 2,
    ) -> None:
        self._session = session
        self._llm_concurrency = llm_concurrency
        self._decomposer = decomposer
        self._evaluator = evaluator
        self._retriever = retriever
        self._reranker = reranker
        self._retrieval_top_k = retrieval_top_k
        self._reranker_top_k = reranker_top_k
        self._entailment_max_candidates = entailment_max_candidates
        self._fallback_enabled = fallback_enabled
        self._on_progress = on_progress
        self._llm_call_count = 0

    async def match_requirements(
        self,
        application_id: str,
        user_id: str,
        cv_file_id: str,
        requirements: tuple[VacancyRequirementRow, ...],
    ) -> ClaimPipelineResult:
        """Run claim-based matching for all requirements in parallel."""
        semaphore = asyncio.Semaphore(self._llm_concurrency)
        completed_count = 0
        total = len(requirements)
        progress_lock = asyncio.Lock()

        async def _process_one(
            req_idx: int,
            requirement: VacancyRequirementRow,
        ) -> tuple[
            RequirementClaimResults,
            RequirementAssessment | None,
            list[dict[str, object]],
            dict[str, int],
        ]:
            async with semaphore:
                result = await self._match_single_requirement(
                    user_id,
                    cv_file_id,
                    requirement,
                )
            assessment = self._build_assessment(requirement, result)
            local_gaps: list[dict[str, object]] = []
            local_counts: dict[str, int] = {}
            for cr in result.claim_results:
                local_gaps.append(
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
                local_counts[cr.relation] = local_counts.get(cr.relation, 0) + 1
            nonlocal completed_count
            async with progress_lock:
                completed_count += 1
                if self._on_progress is not None:
                    self._on_progress(completed_count, total)
            return result, assessment, local_gaps, local_counts

        tasks = [
            asyncio.create_task(_process_one(idx, req)) for idx, req in enumerate(requirements)
        ]
        try:
            raw_results = await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )
        finally:
            # A batched evaluator keeps a background flusher task; it must be
            # drained and stopped once every requirement finished. Non-batched
            # evaluators have no close() and this is a no-op.
            close_evaluator = getattr(self._evaluator, "close", None)
            if close_evaluator is not None:
                try:
                    await close_evaluator()
                except Exception as error:
                    logger.warning(
                        "entailment_evaluator_close_failed",
                        error_type=type(error).__name__,
                    )

        assessments: list[RequirementAssessment] = []
        requirement_results: list[RequirementClaimResults] = []
        gap_inputs: list[dict[str, object]] = []
        relation_counts: dict[str, int] = {
            "entailed": 0,
            "partial": 0,
            "related_but_insufficient": 0,
            "insufficient_evidence": 0,
            "evaluation_error": 0,
            "unknown": 0,
            "contradicted": 0,
        }

        for req_idx, item in enumerate(raw_results):
            if isinstance(item, BaseException):
                requirement = requirements[req_idx]
                logger.warning(
                    "claim_matching_failed_for_requirement",
                    requirement_id=requirement.id,
                    error_type=type(item).__name__,
                )
                metrics.increment("claim_matching_requirement_failures")
                assessments.append(
                    RequirementAssessment(
                        requirement_id=requirement.id,
                        requirement_type=RequirementType(
                            requirement.requirement_type,
                        ),
                        importance=RequirementImportance(
                            requirement.importance,
                        ),
                        weight=requirement.weight,
                        is_blocker=requirement.is_blocker,
                        match_level=MatchLevel.EVALUATION_ERROR,
                        entailment_relation=(EntailmentRelation.EVALUATION_ERROR),
                    )
                )
            else:
                req_result, assessment, local_gaps, local_counts = item
                requirement_results.append(req_result)
                if assessment is not None:
                    assessments.append(assessment)
                gap_inputs.extend(local_gaps)
                for rel, cnt in local_counts.items():
                    relation_counts[rel] = relation_counts.get(rel, 0) + cnt

        for relation, count in relation_counts.items():
            metrics.set_gauge(
                f"claims_{relation}",
                float(count),
            )
        metrics.set_gauge(
            "claims_total",
            float(sum(relation_counts.values())),
        )

        gap_analysis = analyze_gaps(gap_inputs) if gap_inputs else None

        logger.info(
            "claim_pipeline_complete",
            application_id=application_id,
            requirement_count=total,
            assessment_count=len(assessments),
            relation_counts=relation_counts,
            gap_count=len(gap_inputs) if gap_inputs else 0,
        )

        return ClaimPipelineResult(
            assessments=assessments,
            requirement_results=requirement_results,
            gap_analysis=gap_analysis,
        )

    async def _match_single_requirement(
        self,
        user_id: str,
        cv_file_id: str,
        requirement: VacancyRequirementRow,
    ) -> RequirementClaimResults:
        decompose_started = time.perf_counter()
        decomposition = await self._decomposer.decompose(
            requirement_id=requirement.id,
            requirement_text=requirement.requirement_text,
            requirement_type=requirement.requirement_type,
            importance=requirement.importance,
            is_blocker=requirement.is_blocker,
        )
        decompose_duration = time.perf_counter() - decompose_started
        self._llm_call_count += 1
        metrics.observe("claim_decomposition_duration_seconds", decompose_duration)
        metrics.set_gauge("claims_per_requirement", float(len(decomposition.claims)))

        if not decomposition.claims:
            return RequirementClaimResults(
                requirement_id=requirement.id,
                requirement_text=requirement.requirement_text,
                overall_relation="insufficient_evidence",
                match_level=MatchLevel.INSUFFICIENT_EVIDENCE,
            )

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
                metrics.increment("claim_evaluator_errors")
                claim_results.append(
                    ClaimMatchResult(
                        claim=claim,
                        relation="evaluation_error",
                        evidence_strength=0.0,
                        has_evidence=False,
                    )
                )

        return self._aggregate_claims(requirement, decomposition, claim_results)

    async def _evaluate_single_claim(
        self,
        user_id: str,
        cv_file_id: str,
        claim: AtomicClaim,
    ) -> ClaimMatchResult:
        """Retrieve evidence and evaluate entailment for one claim."""
        # Duration claims use deterministic evaluation
        if claim.claim_type is ClaimType.EXPERIENCE_DURATION:
            return await self._evaluate_duration_claim(user_id, cv_file_id, claim)

        claim_text = f"{claim.normalized_subject} {claim.claim_type.value}".strip()

        retrieval_started = time.perf_counter()
        candidates = await self._retriever.retrieve(
            claim_text,
            user_id=user_id,
            cv_file_id=cv_file_id,
        )
        metrics.observe("claim_retrieval_duration_seconds", time.perf_counter() - retrieval_started)
        metrics.set_gauge("retrieval_candidate_count", float(len(candidates)))

        if not candidates:
            metrics.increment("claims_no_evidence_retrieved")
            return ClaimMatchResult(
                claim=claim,
                relation="insufficient_evidence",
                evidence_strength=0.0,
                has_evidence=False,
            )

        reranker_available = True
        try:
            reranked = await self._reranker.rerank(claim_text, candidates)
        except Exception:
            if not self._fallback_enabled:
                raise
            reranker_available = False
            metrics.increment("reranker_fallback")
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

        top_candidates = reranked[: min(self._reranker_top_k, self._entailment_max_candidates)]
        best_entailment: EntailmentResult | None = None
        all_entailments: list[EntailmentResult] = []
        supporting_evidence_ids: list[str] = []
        had_eval_errors = False

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
                    claim_subject=claim.normalized_subject,
                    claim_criticality=claim.criticality.value,
                    source_requirement=claim.source_text,
                )
                self._llm_call_count += 1
                all_entailments.append(eval_result)
                if eval_result.relation in (
                    EntailmentRelation.ENTAILED,
                    EntailmentRelation.PARTIAL,
                ):
                    supporting_evidence_ids.append(evidence.id)

                if (
                    best_entailment is None
                    or eval_result.evidence_strength > best_entailment.evidence_strength
                ):
                    best_entailment = eval_result

                # Early exit: a strong, direct confirmation is conclusive. Evaluating
                # the remaining candidates only adds latency, not better evidence.
                if (
                    eval_result.relation is EntailmentRelation.ENTAILED
                    and eval_result.coverage >= _EARLY_EXIT_COVERAGE
                ):
                    break
            except Exception as error:
                had_eval_errors = True
                logger.warning(
                    "entailment_evaluation_failed",
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    error_type=type(error).__name__,
                )
                metrics.increment("claim_evaluator_errors")

        # If all evaluations failed technically, report evaluation_error
        if best_entailment is None and had_eval_errors:
            return ClaimMatchResult(
                claim=claim,
                relation="evaluation_error",
                evidence_strength=0.0,
                has_evidence=bool(candidates),
            )

        # If no evaluation succeeded at all (e.g. all evidence was None)
        if best_entailment is None:
            return ClaimMatchResult(
                claim=claim,
                relation="insufficient_evidence",
                evidence_strength=0.0,
                has_evidence=False,
            )

        synthesized = None
        if len(supporting_evidence_ids) > 1 and best_entailment is not None:
            synthesized = SynthesizedEvidence(
                evidence_ids=supporting_evidence_ids,
                best_entailment=best_entailment,
                contributing_entailments=[
                    e for e in all_entailments if e.evidence_id in supporting_evidence_ids
                ],
            )

        return ClaimMatchResult(
            claim=claim,
            best_entailment=best_entailment,
            relation=best_entailment.relation.value,
            evidence_strength=best_entailment.evidence_strength,
            has_evidence=True,
            synthesized=synthesized,
        )

    async def _evaluate_duration_claim(
        self,
        user_id: str,
        cv_file_id: str,
        claim: AtomicClaim,
    ) -> ClaimMatchResult:
        """Evaluate a duration claim using deterministic interval math."""
        claim_text = f"{claim.normalized_subject} experience".strip()
        candidates = await self._retriever.retrieve(
            claim_text,
            user_id=user_id,
            cv_file_id=cv_file_id,
        )

        intervals: list[ExperienceInterval] = []
        for candidate in candidates[: self._retrieval_top_k]:
            evidence = self._session.get(CandidateEvidenceRow, candidate.evidence_id)
            if evidence is None:
                continue
            if evidence.years is not None and evidence.years > 0:
                from datetime import date as _date

                intervals.append(
                    ExperienceInterval(
                        skill=claim.normalized_subject,
                        started_at=_date(_date.today().year - int(evidence.years), 1, 1),
                        ended_at=None,
                        source_evidence_id=evidence.id,
                    )
                )

        required_years = 0.0
        if claim.required_value:
            with contextlib.suppress(ValueError, TypeError):
                required_years = float(claim.required_value)

        duration_result = evaluate_duration(
            claim.id,
            required_years,
            intervals,
        )

        # Map duration status to entailment relation — never use "unknown" for duration
        if duration_result.status == "matched":
            relation = "entailed"
            strength = min(1.0, 0.7 + 0.3 * (duration_result.actual_years / max(required_years, 1)))
        elif duration_result.status == "partial":
            relation = "partial"
            strength = 0.4 + 0.3 * (duration_result.actual_years / max(required_years, 1))
        else:
            # insufficient_evidence — dates not available, NOT "missing skill"
            relation = "insufficient_evidence"
            strength = 0.0

        duration_dict = {
            "claim": f"{claim.subject} >= {required_years} years",
            "required_years": required_years,
            "actual_years": duration_result.actual_years,
            "status": duration_result.status,
            "source_intervals": duration_result.source_intervals,
        }

        metrics.observe("duration_evaluator_duration_seconds", 0.0)

        return ClaimMatchResult(
            claim=claim,
            relation=relation,
            evidence_strength=round(strength, 4),
            has_evidence=bool(intervals),
            duration_result=duration_dict,
        )

    def _aggregate_claims(
        self,
        requirement: VacancyRequirementRow,
        decomposition: RequirementDecomposition,
        claim_results: list[ClaimMatchResult],
    ) -> RequirementClaimResults:
        """Aggregate claim results into a requirement-level assessment.

        Handles AND/OR semantics, criticality, and distinguishes between
        'missing' (no evidence found), 'insufficient_evidence' (data unclear),
        and 'evaluation_error' (technical failure).
        """
        if not claim_results:
            return RequirementClaimResults(
                requirement_id=requirement.id,
                requirement_text=requirement.requirement_text,
                overall_relation="insufficient_evidence",
                match_level=MatchLevel.INSUFFICIENT_EVIDENCE,
            )

        required_claims = [
            cr for cr in claim_results if cr.claim.criticality.value in ("required", "hard_blocker")
        ]
        if not required_claims:
            required_claims = claim_results

        # Handle OR groups
        or_groups: dict[str, list[ClaimMatchResult]] = {}
        non_or_claims: list[ClaimMatchResult] = []
        for cr in required_claims:
            if cr.claim.logical_group.value == "or":
                group_key = cr.claim.requirement_id + "_or"
                or_groups.setdefault(group_key, []).append(cr)
            else:
                non_or_claims.append(cr)

        resolved_relations = []
        for group in or_groups.values():
            group_relations = [cr.relation for cr in group]
            if "entailed" in group_relations:
                resolved_relations.append("entailed")
            elif "partial" in group_relations:
                resolved_relations.append("partial")
            elif "related_but_insufficient" in group_relations:
                resolved_relations.append("related_but_insufficient")
            elif "insufficient_evidence" in group_relations:
                resolved_relations.append("insufficient_evidence")
            elif "evaluation_error" in group_relations:
                resolved_relations.append("evaluation_error")
            else:
                resolved_relations.append("unknown")

        for cr in non_or_claims:
            resolved_relations.append(cr.relation)

        if not resolved_relations:
            resolved_relations = [cr.relation for cr in claim_results]

        relations = resolved_relations
        strengths = [cr.evidence_strength for cr in claim_results]

        overall_relation = self._determine_overall_relation(relations)

        # AND aggregation: 0.65 * min + 0.35 * weighted_avg
        # OR aggregation: max (already handled above by selecting best from OR groups)
        if strengths:
            min_strength = min(strengths)
            avg_strength = sum(strengths) / len(strengths)
            overall_strength = 0.65 * min_strength + 0.35 * avg_strength
        else:
            overall_strength = 0.0

        _RELATION_TO_LEVEL = {
            "entailed": MatchLevel.EXACT,
            "partial": MatchLevel.PARTIAL,
            "related_but_insufficient": MatchLevel.RELATED,
            "insufficient_evidence": MatchLevel.INSUFFICIENT_EVIDENCE,
            "evaluation_error": MatchLevel.EVALUATION_ERROR,
            "unknown": MatchLevel.MISSING,
            "contradicted": MatchLevel.MISSING,
        }
        match_level = _RELATION_TO_LEVEL.get(overall_relation, MatchLevel.MISSING)

        is_hard_blocker = any(cr.claim.criticality.value == "hard_blocker" for cr in claim_results)

        is_ru = _is_cyrillic(requirement.requirement_text)
        labels = _RELATION_LABELS_RU if is_ru else _RELATION_LABELS_EN
        header = (
            f"Разложено на {len(decomposition.claims)} утверждений:"
            if is_ru
            else f"Decomposed into {len(decomposition.claims)} claims:"
        )
        lines = [header]
        for cr in claim_results:
            symbol = _RELATION_SYMBOLS.get(cr.relation, "?")
            label = labels.get(cr.relation, cr.relation)
            synth_note = ""
            if cr.synthesized and len(cr.synthesized.evidence_ids) > 1:
                synth_note = (
                    f" [синтезировано из {len(cr.synthesized.evidence_ids)} источников]"
                    if is_ru
                    else f" [synthesized from {len(cr.synthesized.evidence_ids)} evidence]"
                )
            duration_note = ""
            if cr.duration_result:
                dr = cr.duration_result
                duration_note = (
                    f" [стаж: {dr.get('actual_years', 0):.1f}/"
                    f"{dr.get('required_years', 0):.1f} лет]"
                    if is_ru
                    else (
                        f" [duration: {dr.get('actual_years', 0):.1f}/"
                        f"{dr.get('required_years', 0):.1f} years]"
                    )
                )
            lines.append(
                f"  {symbol} {cr.claim.subject}: {label} "
                f"(strength={cr.evidence_strength:.2f}){synth_note}{duration_note}"
            )

        return RequirementClaimResults(
            requirement_id=requirement.id,
            requirement_text=requirement.requirement_text,
            claim_results=claim_results,
            overall_relation=overall_relation,
            overall_strength=round(overall_strength, 4),
            match_level=match_level,
            is_hard_blocker=is_hard_blocker,
            explanation="\n".join(lines),
        )

    @staticmethod
    def _determine_overall_relation(relations: list[str]) -> str:
        """Determine overall requirement relation from claim relations.

        Key changes from v1:
        - INSUFFICIENT_EVIDENCE is NOT treated as MISSING
        - EVALUATION_ERROR is NOT treated as MISSING
        - Only truly missing/unknown claims count as "missing"
        """
        if all(r == "entailed" for r in relations):
            return "entailed"

        has_entailed = any(r == "entailed" for r in relations)
        has_partial = any(r == "partial" for r in relations)
        has_related = any(r == "related_but_insufficient" for r in relations)
        has_insufficient = any(r == "insufficient_evidence" for r in relations)
        has_error = any(r == "evaluation_error" for r in relations)
        has_missing = any(r in ("unknown", "contradicted", "missing") for r in relations)

        # If all claims are either entailed or insufficient/error → partial
        if has_entailed and not has_missing and not has_related:
            return "partial"

        if has_entailed or has_partial:
            return "partial"

        # All claims are insufficient or error — not "missing", it's uncertain
        if has_insufficient and not has_missing and not has_related:
            return "insufficient_evidence"

        if has_error and not has_missing and not has_related:
            return "evaluation_error"

        if has_related:
            return "related_but_insufficient"

        if has_insufficient:
            return "insufficient_evidence"

        if has_error:
            return "evaluation_error"

        return "unknown"

    def _build_assessment(
        self,
        requirement: VacancyRequirementRow,
        req_result: RequirementClaimResults,
    ) -> RequirementAssessment:
        entailment_relation = None
        entailment_values = set(EntailmentRelation.__members__.values())
        if req_result.overall_relation in entailment_values:
            entailment_relation = EntailmentRelation(req_result.overall_relation)

        experience_level = None
        best_entailment = None
        for cr in req_result.claim_results:
            if cr.best_entailment is not None and (
                best_entailment is None or cr.evidence_strength > best_entailment.evidence_strength
            ):
                best_entailment = cr.best_entailment

        is_unresolved = req_result.is_hard_blocker and req_result.overall_relation in (
            "insufficient_evidence",
            "evaluation_error",
        )

        # Compute average coverage across claims for this requirement
        coverages = [
            cr.best_entailment.coverage
            for cr in req_result.claim_results
            if cr.best_entailment is not None and cr.best_entailment.coverage > 0
        ]
        claim_coverage = sum(coverages) / len(coverages) if coverages else None

        return RequirementAssessment(
            requirement_id=requirement.id,
            requirement_type=RequirementType(requirement.requirement_type),
            importance=RequirementImportance(requirement.importance),
            weight=requirement.weight,
            is_blocker=requirement.is_blocker,
            is_hard_blocker=req_result.is_hard_blocker and not is_unresolved,
            is_unresolved_blocker=is_unresolved,
            match_level=req_result.match_level,
            entailment_relation=entailment_relation,
            evidence_strength=req_result.overall_strength,
            evidence_experience_level=experience_level,
            claim_coverage=claim_coverage,
        )
