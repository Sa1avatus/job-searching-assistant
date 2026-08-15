"""Vacancy-candidate matching pipeline.

Coordinates extraction, indexing, retrieval, reranking, scoring, and optional
RAG enrichment.  PostgreSQL owns requirements, candidate evidence, embedding
metadata, per-requirement matches, and aggregate results.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.matching.claim_pipeline import ClaimMatchPipeline, ClaimPipelineResult
from app.matching.claims import RequirementDecomposer
from app.matching.entailment import EvidenceEvaluator
from app.matching.extraction import (
    CandidateEvidenceExtractor,
    EvidenceType,
    ExperienceLevel,
    RequirementImportance,
    RequirementType,
    VacancyRequirementExtractor,
)
from app.matching.normalization import SkillNormalizer
from app.matching.rag_client import RagClient
from app.matching.relevance import (
    HardRelevanceGate,
    RelevanceDecision,
    RelevanceEvidence,
    RelevanceRequirement,
    RelevanceResult,
)
from app.matching.scoring import (
    DeterministicMatchScorer,
    DeterministicScore,
    MatchLevel,
    RequirementAssessment,
)
from app.matching.semantic import RerankedCandidate, Reranker, RetrievalCandidate
from app.matching.vacancy_source import VACANCY_MATCHING_SOURCE_VERSION
from app.observability.metrics import metrics
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CandidateEvidenceRow,
    CvFileRow,
    RequirementMatchRow,
    VacancyRequirementRow,
    VacancyRow,
)

logger = structlog.get_logger(__name__)


class MatchingEntityNotFoundError(LookupError):
    pass


class MissingSelectedCvError(ValueError):
    pass


class RequirementRetriever(Protocol):
    async def retrieve(
        self,
        requirement_text: str,
        *,
        user_id: str,
        cv_file_id: str,
    ) -> tuple[RetrievalCandidate, ...]: ...


class EvidenceIndexer(Protocol):
    async def index_cv(self, *, user_id: str, cv_file_id: str) -> int: ...


class MatchingPipeline:
    def __init__(
        self,
        session: Session,
        vacancy_extractor: VacancyRequirementExtractor,
        candidate_extractor: CandidateEvidenceExtractor,
        retriever: RequirementRetriever,
        reranker: Reranker,
        scorer: DeterministicMatchScorer,
        *,
        evidence_indexer: EvidenceIndexer | None = None,
        relevance_gate: HardRelevanceGate | None = None,
        skill_normalizer: SkillNormalizer | None = None,
        rag_client: RagClient | None = None,
        claim_decomposer: RequirementDecomposer | None = None,
        evidence_evaluator: EvidenceEvaluator | None = None,
        retrieval_top_k: int = 20,
        reranker_top_k: int = 5,
        shadow_mode: bool = True,
        fallback_enabled: bool = True,
        llm_concurrency: int = 10,
        entailment_max_candidates: int = 2,
    ) -> None:
        self._session = session
        self._vacancy_extractor = vacancy_extractor
        self._candidate_extractor = candidate_extractor
        self._retriever = retriever
        self._reranker = reranker
        self._scorer = scorer
        self._evidence_indexer = evidence_indexer
        self._relevance_gate = relevance_gate or HardRelevanceGate()
        self._skill_normalizer = skill_normalizer or SkillNormalizer()
        self._rag_client = rag_client
        self._claim_decomposer = claim_decomposer
        self._evidence_evaluator = evidence_evaluator
        self._retrieval_top_k = retrieval_top_k
        self._reranker_top_k = reranker_top_k
        self._shadow_mode = shadow_mode
        self._fallback_enabled = fallback_enabled
        # Claim pipeline instance (created when both decomposer and evaluator are available)
        self._claim_pipeline: ClaimMatchPipeline | None = None
        if claim_decomposer is not None and evidence_evaluator is not None:
            self._claim_pipeline = ClaimMatchPipeline(
                session=session,
                decomposer=claim_decomposer,
                evaluator=evidence_evaluator,
                retriever=retriever,
                reranker=reranker,
                retrieval_top_k=retrieval_top_k,
                reranker_top_k=reranker_top_k,
                fallback_enabled=fallback_enabled,
                llm_concurrency=llm_concurrency,
                entailment_max_candidates=entailment_max_candidates,
            )

    async def match(
        self,
        application_id: str,
        *,
        vacancy_source_text: str,
        cv_source_text: str,
    ) -> ApplicationMatchResultRow:
        application = self._session.get(ApplicationRow, application_id)
        if application is None:
            raise MatchingEntityNotFoundError("Application not found")
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            raise MatchingEntityNotFoundError("Vacancy not found")
        if application.selected_cv_file_id is None:
            raise MissingSelectedCvError("Application has no selected CV")
        cv_file = self._session.get(CvFileRow, application.selected_cv_file_id)
        if cv_file is None or cv_file.user_id != application.user_id:
            raise MissingSelectedCvError("Selected CV does not belong to the application user")

        run_id = str(uuid.uuid4())
        total_started = time.perf_counter()
        extraction_duration = 0.0
        indexing_duration = 0.0
        self._start_run(application, cv_file, run_id)
        try:
            self._set_status(application.id, "extracting")
            extraction_started = time.perf_counter()
            requirement_rows = await self._extract_requirements(vacancy, vacancy_source_text)
            evidence_rows = await self._extract_evidence(
                application,
                cv_file,
                cv_source_text,
            )
            extraction_duration = time.perf_counter() - extraction_started
            metrics.observe("extraction_duration_seconds", extraction_duration)
            metrics.set_gauge("requirements_per_vacancy", float(len(requirement_rows)))
            self._session.flush()
            self._session.execute(
                delete(RequirementMatchRow).where(
                    RequirementMatchRow.application_id == application.id
                )
            )
            relevance = self._evaluate_relevance(requirement_rows, evidence_rows)
            if relevance.decision is RelevanceDecision.REJECT:
                aggregate = self._store_rejected_aggregate(
                    application,
                    cv_file,
                    requirement_rows,
                    relevance,
                )
                if not self._shadow_mode:
                    application.match_score = round(aggregate.final_score)
                aggregate.status = "scored"
                self._session.commit()
                metrics.increment("matching_runs_total")
                metrics.increment("hard_relevance_rejections_total")
                metrics.observe("matching_duration_seconds", time.perf_counter() - total_started)
                logger.info(
                    "matching_run_rejected_by_hard_relevance_gate",
                    run_id=run_id,
                    application_id=application.id,
                    vacancy_id=vacancy.id,
                    reason_codes=[reason.value for reason in relevance.reasons],
                )
                return aggregate
            if self._evidence_indexer is not None:
                self._set_status(application.id, "indexing")
                indexing_started = time.perf_counter()
                await self._evidence_indexer.index_cv(
                    user_id=application.user_id,
                    cv_file_id=cv_file.id,
                )
                indexing_duration = time.perf_counter() - indexing_started
                metrics.observe("embedding_duration_seconds", indexing_duration)
                metrics.set_gauge("evidence_index_size", float(len(evidence_rows)))

            self._set_status(application.id, "matching")
            self._set_progress(application.id, requirements_total=len(requirement_rows))

            # Choose pipeline: claim-based or legacy
            assessments, claim_result = await self._run_matching(
                application, cv_file, requirement_rows
            )

            deterministic_score = self._scorer.score(tuple(assessments))
            self._session.flush()
            rag_context = await self._fetch_rag_context(vacancy, application)

            # Build explanation with gap analysis if available
            gap_analysis_dict = None
            if claim_result and claim_result.gap_analysis:
                gap_analysis_dict = {
                    "total_gaps": claim_result.gap_analysis.total_gaps,
                    "skill_gaps": claim_result.gap_analysis.skill_gaps,
                    "evidence_gaps": claim_result.gap_analysis.evidence_gaps,
                    "duration_gaps": claim_result.gap_analysis.duration_gaps,
                    "metadata_gaps": claim_result.gap_analysis.gap_analysis
                    if hasattr(claim_result.gap_analysis, "gap_analysis")
                    else 0,
                    "gaps": [
                        {
                            "gap_type": g.gap_type.value,
                            "claim_subject": g.claim_subject,
                            "description": g.description,
                            "suggested_action": g.suggested_action,
                        }
                        for g in claim_result.gap_analysis.gaps[:20]
                    ],
                }

            aggregate = self._store_aggregate(
                application,
                cv_file,
                deterministic_score,
                relevance=relevance,
                assessments=tuple(assessments),
                rag_context=rag_context,
                gap_analysis=gap_analysis_dict,
            )
            if not self._shadow_mode:
                application.match_score = deterministic_score.final_score
            aggregate.status = "scored"
            aggregate.failure_reason = None
            aggregate.fallback_reason = None
            self._session.commit()
            total_duration = time.perf_counter() - total_started
            metrics.increment("matching_runs_total")
            metrics.observe("matching_duration_seconds", total_duration)
            logger.info(
                "matching_run_completed",
                run_id=run_id,
                application_id=application.id,
                vacancy_id=vacancy.id,
                user_id=application.user_id,
                extraction_duration_seconds=extraction_duration,
                embedding_duration_seconds=indexing_duration,
                total_duration_seconds=total_duration,
                requirement_count=len(requirement_rows),
                evidence_count=len(evidence_rows),
                fallback_used=False,
                scoring_version=aggregate.scoring_version,
                model_versions=aggregate.model_versions_json,
                claim_pipeline_used=self._claim_pipeline is not None,
            )
            return aggregate
        except Exception as error:
            self._session.rollback()
            failed_aggregate = self._session.get(ApplicationMatchResultRow, application.id)
            if failed_aggregate is not None:
                failed_aggregate.status = "degraded" if self._fallback_enabled else "failed"
                failed_aggregate.failure_reason = f"{type(error).__name__}: {str(error)[:900]}"
                failed_aggregate.fallback_reason = (
                    "Legacy match_score retained because matching v2 failed"
                    if self._fallback_enabled
                    else None
                )
                self._session.commit()
            total_duration = time.perf_counter() - total_started
            metrics.increment("matching_runs_total")
            metrics.increment("matching_failures_total")
            metrics.observe("matching_duration_seconds", total_duration)
            if self._fallback_enabled:
                metrics.increment("degraded_matches_total")
            logger.warning(
                "matching_run_failed",
                run_id=run_id,
                application_id=application.id,
                vacancy_id=vacancy.id,
                user_id=application.user_id,
                total_duration_seconds=total_duration,
                failure_type=type(error).__name__,
                fallback_used=self._fallback_enabled,
            )
            raise

    async def _run_matching(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        requirement_rows: tuple[VacancyRequirementRow, ...],
    ) -> tuple[list[RequirementAssessment], ClaimPipelineResult | None]:
        """Run matching through claim pipeline if available, else legacy."""
        if (
            self._claim_pipeline is not None
            and self._claim_decomposer is not None
            and self._evidence_evaluator is not None
        ):
            try:
                # Wire up progress tracking
                def _on_requirement_progress(processed: int, total: int) -> None:
                    self._set_progress(
                        application.id,
                        requirements_processed=processed,
                        llm_calls_delta=self._claim_pipeline._llm_call_count,
                    )
                    self._claim_pipeline._llm_call_count = 0

                self._claim_pipeline._on_progress = _on_requirement_progress
                claim_result = await self._claim_pipeline.match_requirements(
                    application_id=application.id,
                    user_id=application.user_id,
                    cv_file_id=cv_file.id,
                    requirements=requirement_rows,
                )
                # Persist requirement match rows with claim details
                for req_result in claim_result.requirement_results:
                    best_entailment = None
                    for cr in req_result.claim_results:
                        if cr.best_entailment is not None and (
                            best_entailment is None
                            or cr.evidence_strength > best_entailment.evidence_strength
                        ):
                            best_entailment = cr.best_entailment

                    evaluator_failure_codes = sorted(
                        {
                            cr.best_entailment.error_type
                            for cr in req_result.claim_results
                            if cr.best_entailment is not None
                            and cr.best_entailment.error_type is not None
                        }
                    )

                    self._session.add(
                        RequirementMatchRow(
                            application_id=application.id,
                            requirement_id=req_result.requirement_id,
                            evidence_id=best_entailment.evidence_id if best_entailment else None,
                            lexical_score=None,
                            dense_score=None,
                            hybrid_score=best_entailment.semantic_score
                            if best_entailment
                            else None,
                            reranker_raw_score=None,
                            reranker_score=best_entailment.reranker_score
                            if best_entailment
                            else None,
                            final_match_score=round(req_result.overall_strength * 100, 4),
                            match_level=req_result.match_level.value,
                            explanation=req_result.explanation,
                            retrieval_model_versions_json={
                                "pipeline": "claim-based",
                                "evaluator_failure_codes": evaluator_failure_codes,
                                "decomposer": {
                                    "name": self._claim_decomposer.model_name,
                                    "version": self._claim_decomposer.model_version,
                                },
                                "evaluator": {
                                    "name": self._evidence_evaluator.model_name,
                                    "version": self._evidence_evaluator.model_version,
                                },
                                "reranker": {
                                    "name": self._reranker.model_name,
                                    "revision": self._reranker.model_revision,
                                },
                            },
                            entailment_relation=req_result.overall_relation,
                            evidence_strength=req_result.overall_strength,
                            is_hard_blocker=req_result.is_hard_blocker,
                        )
                    )
                metrics.increment("claim_pipeline_runs_total")
                return claim_result.assessments, claim_result
            except Exception as error:
                logger.warning(
                    "claim_pipeline_failed_falling_back_to_legacy",
                    error_type=type(error).__name__,
                )
                metrics.increment("claim_pipeline_fallback_total")

        # Legacy path
        assessments = []
        for requirement in requirement_rows:
            assessment = await self._match_requirement_legacy(application, cv_file, requirement)
            assessments.append(assessment)
        return assessments, None

    async def _match_requirement_legacy(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        requirement: VacancyRequirementRow,
    ) -> RequirementAssessment:
        """Legacy whole-requirement matching (kept for backward compatibility)."""
        retrieval_started = time.perf_counter()
        candidates = await self._retriever.retrieve(
            requirement.normalized_text,
            user_id=application.user_id,
            cv_file_id=cv_file.id,
        )
        metrics.observe("retrieval_duration_seconds", time.perf_counter() - retrieval_started)
        metrics.set_gauge("reranker_candidates_per_requirement", float(len(candidates)))
        reranker_started = time.perf_counter()
        reranker_available = True
        try:
            reranked = await self._reranker.rerank(requirement.normalized_text, candidates)
        except Exception as error:
            if not self._fallback_enabled:
                raise
            reranker_available = False
            metrics.increment("reranker_unavailable_total")
            logger.warning(
                "reranker_unavailable",
                application_id=application.id,
                requirement_id=requirement.id,
                failure_type=type(error).__name__,
            )
            reranked = tuple(
                RerankedCandidate(
                    candidate=candidate,
                    raw_score=candidate.hybrid_score,
                    normalized_score=candidate.hybrid_score,
                )
                for candidate in sorted(
                    candidates,
                    key=lambda item: (item.hybrid_score, item.evidence_id),
                    reverse=True,
                )
            )
        metrics.observe("reranker_duration_seconds", time.perf_counter() - reranker_started)
        best_candidate = reranked[0] if reranked else None
        evidence = (
            self._session.get(CandidateEvidenceRow, best_candidate.candidate.evidence_id)
            if best_candidate is not None
            else None
        )
        match_level = self._classify_match(
            best_candidate.normalized_score if best_candidate is not None else 0,
            evidence,
            is_blocker=requirement.is_blocker,
        )
        final_match_score = (
            round(100 * best_candidate.normalized_score, 4) if best_candidate is not None else 0.0
        )
        self._session.add(
            RequirementMatchRow(
                application_id=application.id,
                requirement_id=requirement.id,
                evidence_id=evidence.id if evidence is not None else None,
                lexical_score=(
                    best_candidate.candidate.lexical_score if best_candidate is not None else None
                ),
                dense_score=(
                    best_candidate.candidate.dense_score if best_candidate is not None else None
                ),
                hybrid_score=(
                    best_candidate.candidate.hybrid_score if best_candidate is not None else None
                ),
                reranker_raw_score=(
                    best_candidate.raw_score
                    if best_candidate is not None and reranker_available
                    else None
                ),
                reranker_score=(
                    best_candidate.normalized_score
                    if best_candidate is not None and reranker_available
                    else None
                ),
                final_match_score=final_match_score,
                match_level=match_level.value,
                explanation=self._requirement_explanation(match_level, evidence),
                retrieval_model_versions_json={
                    "pipeline": "legacy",
                    "reranker": {
                        "name": self._reranker.model_name,
                        "revision": self._reranker.model_revision,
                        "status": "available" if reranker_available else "reranker_unavailable",
                    },
                },
            )
        )
        return RequirementAssessment(
            requirement_id=requirement.id,
            requirement_type=RequirementType(requirement.requirement_type),
            importance=RequirementImportance(requirement.importance),
            weight=requirement.weight,
            is_blocker=requirement.is_blocker,
            match_level=match_level,
            evidence_experience_level=(
                ExperienceLevel(evidence.experience_level) if evidence is not None else None
            ),
        )

    def _start_run(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        run_id: str,
    ) -> None:
        aggregate = self._session.get(ApplicationMatchResultRow, application.id)
        if aggregate is None:
            aggregate = ApplicationMatchResultRow(application_id=application.id)
            self._session.add(aggregate)
        aggregate.cv_file_id = cv_file.id
        aggregate.run_id = run_id
        aggregate.status = "pending"
        aggregate.eligibility_status = "pending"
        aggregate.failure_reason = None
        aggregate.fallback_reason = None
        aggregate.started_at = datetime.now(UTC)
        aggregate.calculated_at = None
        aggregate.requirements_total = 0
        aggregate.requirements_processed = 0
        aggregate.llm_calls_made = 0
        self._session.commit()

    def _set_status(self, application_id: str, status: str) -> None:
        aggregate = self._session.get(ApplicationMatchResultRow, application_id)
        if aggregate is None:
            raise MatchingEntityNotFoundError("Matching run state not found")
        aggregate.status = status
        self._session.commit()

    def _set_progress(
        self,
        application_id: str,
        *,
        requirements_total: int | None = None,
        requirements_processed: int | None = None,
        llm_calls_delta: int = 0,
    ) -> None:
        aggregate = self._session.get(ApplicationMatchResultRow, application_id)
        if aggregate is None:
            return
        if requirements_total is not None:
            aggregate.requirements_total = requirements_total
        if requirements_processed is not None:
            aggregate.requirements_processed = requirements_processed
        if llm_calls_delta:
            aggregate.llm_calls_made = (aggregate.llm_calls_made or 0) + llm_calls_delta
        self._session.commit()

    async def _extract_requirements(
        self,
        vacancy: VacancyRow,
        source_text: str,
    ) -> tuple[VacancyRequirementRow, ...]:
        extraction_run_id = self._extraction_run_id(
            entity_type="vacancy",
            entity_id=vacancy.id,
            source_text=source_text,
            model_name=self._vacancy_extractor.model_name,
            model_version=self._vacancy_extractor.model_version,
            schema_version=self._vacancy_extractor.schema_version,
        )
        existing_rows = tuple(
            self._session.scalars(
                select(VacancyRequirementRow).where(
                    VacancyRequirementRow.vacancy_id == vacancy.id,
                    VacancyRequirementRow.extraction_run_id == extraction_run_id,
                )
            )
        )
        if existing_rows:
            return existing_rows
        extraction = await self._vacancy_extractor.extract(
            vacancy_id=vacancy.id,
            source_text=source_text,
        )
        rows = [
            VacancyRequirementRow(
                vacancy_id=vacancy.id,
                requirement_text=requirement.text,
                normalized_text=self._skill_normalizer.normalize(
                    requirement.normalized_text
                ).canonical,
                requirement_type=requirement.requirement_type.value,
                importance=requirement.importance.value,
                weight=requirement.weight,
                is_blocker=requirement.is_blocker,
                alternatives_json=requirement.alternatives,
                source_fragment=requirement.source_fragment,
                source_section=requirement.source_section,
                extraction_model=self._vacancy_extractor.model_name,
                extraction_model_version=self._vacancy_extractor.model_version,
                extraction_schema_version=self._vacancy_extractor.schema_version,
                extraction_run_id=extraction_run_id,
                confidence=requirement.confidence,
            )
            for requirement in extraction.requirements
        ]
        for skill, importance, section in self._structured_vacancy_skills(vacancy):
            normalized_skill = self._skill_normalizer.normalize(skill).canonical
            matching_row = next(
                (
                    row
                    for row in rows
                    if row.requirement_type == RequirementType.HARD_SKILL.value
                    and self._contains_normalized_skill(row.normalized_text, normalized_skill)
                ),
                None,
            )
            if matching_row is not None:
                if importance is RequirementImportance.REQUIRED:
                    matching_row.importance = RequirementImportance.REQUIRED.value
                    matching_row.weight = max(matching_row.weight, 1.0)
                continue
            rows.append(
                VacancyRequirementRow(
                    vacancy_id=vacancy.id,
                    requirement_text=skill,
                    normalized_text=normalized_skill,
                    requirement_type=RequirementType.HARD_SKILL.value,
                    importance=importance.value,
                    weight=1.0 if importance is RequirementImportance.REQUIRED else 0.6,
                    is_blocker=False,
                    alternatives_json=[],
                    source_fragment=skill,
                    source_section=section,
                    extraction_model="structured-vacancy-fields",
                    extraction_model_version="1",
                    extraction_schema_version="1",
                    extraction_run_id=extraction_run_id,
                    confidence=1.0,
                )
            )
        self._session.add_all(rows)
        return tuple(rows)

    def _structured_vacancy_skills(
        self,
        vacancy: VacancyRow,
    ) -> tuple[tuple[str, RequirementImportance, str], ...]:
        result: list[tuple[str, RequirementImportance, str]] = []
        seen: set[str] = set()
        for skills, importance, section in (
            (
                vacancy.required_skills or (),
                RequirementImportance.REQUIRED,
                "structured_required_skills",
            ),
            (
                vacancy.preferred_skills or (),
                RequirementImportance.PREFERRED,
                "structured_preferred_skills",
            ),
        ):
            for raw_skill in skills:
                skill = " ".join(str(raw_skill).split())
                if not skill:
                    continue
                key = self._skill_normalizer.normalize(skill).canonical.casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append((skill, importance, section))
        return tuple(result)

    @staticmethod
    def _contains_normalized_skill(requirement_text: str, normalized_skill: str) -> bool:
        return (
            re.search(
                rf"(?<!\w){re.escape(normalized_skill.casefold())}(?!\w)",
                requirement_text.casefold(),
            )
            is not None
        )

    async def _extract_evidence(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        source_text: str,
    ) -> tuple[CandidateEvidenceRow, ...]:
        extraction_run_id = self._extraction_run_id(
            entity_type="cv",
            entity_id=cv_file.id,
            source_text=source_text,
            model_name=self._candidate_extractor.model_name,
            model_version=self._candidate_extractor.model_version,
            schema_version=self._candidate_extractor.schema_version,
        )
        existing_rows = tuple(
            self._session.scalars(
                select(CandidateEvidenceRow).where(
                    CandidateEvidenceRow.cv_file_id == cv_file.id,
                    CandidateEvidenceRow.extraction_run_id == extraction_run_id,
                )
            )
        )
        if existing_rows:
            return existing_rows
        extraction = await self._candidate_extractor.extract(
            user_id=application.user_id,
            cv_file_id=cv_file.id,
            source_text=source_text,
            source_is_verified=cv_file.analyzed_at is not None,
        )
        rows = tuple(
            CandidateEvidenceRow(
                user_id=application.user_id,
                cv_file_id=cv_file.id,
                evidence_text=evidence.text,
                normalized_text=evidence.normalized_text,
                evidence_type=evidence.evidence_type.value,
                skill_name=(
                    self._skill_normalizer.normalize(evidence.skill_name).canonical
                    if evidence.skill_name is not None
                    else None
                ),
                experience_level=evidence.experience_level.value,
                years=evidence.years,
                is_verified=evidence.is_verified,
                source_fragment=evidence.source_fragment,
                source_section=evidence.source_section,
                extraction_model=self._candidate_extractor.model_name,
                extraction_model_version=self._candidate_extractor.model_version,
                extraction_schema_version=self._candidate_extractor.schema_version,
                extraction_run_id=extraction_run_id,
                confidence=evidence.confidence,
            )
            for evidence in extraction.evidence
        )
        self._session.add_all(rows)
        return rows

    def _evaluate_relevance(
        self,
        requirements: tuple[VacancyRequirementRow, ...],
        evidence: tuple[CandidateEvidenceRow, ...],
    ) -> RelevanceResult:
        gate_types = {
            RequirementType.ROLE,
            RequirementType.SENIORITY,
            RequirementType.LANGUAGE,
            RequirementType.LOCATION,
            RequirementType.WORK_AUTHORIZATION,
        }
        evidence_type_map = {
            EvidenceType.ROLE: RequirementType.ROLE,
            EvidenceType.SENIORITY: RequirementType.SENIORITY,
            EvidenceType.LANGUAGE: RequirementType.LANGUAGE,
            EvidenceType.LOCATION: RequirementType.LOCATION,
            EvidenceType.AUTHORIZATION: RequirementType.WORK_AUTHORIZATION,
        }
        typed_requirements = tuple(
            RelevanceRequirement(
                requirement_id=row.id,
                requirement_type=requirement_type,
                importance=RequirementImportance(row.importance),
                canonical_value=row.normalized_text,
                is_blocker=row.is_blocker,
            )
            for row in requirements
            if (requirement_type := RequirementType(row.requirement_type)) in gate_types
        )
        typed_evidence: list[RelevanceEvidence] = []
        for row in evidence:
            mapped_requirement_type = evidence_type_map.get(EvidenceType(row.evidence_type))
            if mapped_requirement_type is None:
                continue
            typed_evidence.append(
                RelevanceEvidence(
                    requirement_type=mapped_requirement_type,
                    canonical_value=row.skill_name or row.normalized_text,
                )
            )
        return self._relevance_gate.evaluate(typed_requirements, tuple(typed_evidence))

    def _store_rejected_aggregate(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        requirements: tuple[VacancyRequirementRow, ...],
        relevance: RelevanceResult,
    ) -> ApplicationMatchResultRow:
        rejected_ids = set(relevance.rejected_requirement_ids)
        assessments = tuple(
            RequirementAssessment(
                requirement_id=row.id,
                requirement_type=RequirementType(row.requirement_type),
                importance=RequirementImportance(row.importance),
                weight=row.weight,
                is_blocker=row.is_blocker or row.id in rejected_ids,
                is_hard_blocker=row.is_blocker or row.id in rejected_ids,
                match_level=(MatchLevel.BLOCKER if row.id in rejected_ids else MatchLevel.MISSING),
            )
            for row in requirements
        )
        return self._store_aggregate(
            application,
            cv_file,
            self._scorer.score(assessments),
            relevance=relevance,
            assessments=assessments,
        )

    def _store_aggregate(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        score: DeterministicScore,
        *,
        relevance: RelevanceResult | None = None,
        assessments: tuple[RequirementAssessment, ...] = (),
        rag_context: dict[str, object] | None = None,
        gap_analysis: dict[str, object] | None = None,
    ) -> ApplicationMatchResultRow:
        aggregate = self._session.get(ApplicationMatchResultRow, application.id)
        if aggregate is None:
            aggregate = ApplicationMatchResultRow(application_id=application.id)
            self._session.add(aggregate)
        aggregate.cv_file_id = cv_file.id
        aggregate.eligibility_status = score.eligibility_status.value
        aggregate.final_score = score.final_score
        aggregate.hard_skill_score = score.hard_skill_score
        aggregate.preferred_skill_score = score.preferred_skill_score
        aggregate.role_score = score.role_score
        aggregate.seniority_score = score.seniority_score
        aggregate.experience_score = score.experience_score
        aggregate.work_format_score = score.work_format_score
        aggregate.location_score = score.location_score
        aggregate.domain_score = score.domain_score
        aggregate.language_score = score.language_score
        aggregate.blocker_count = score.blocker_count
        aggregate.matched_required_count = score.matched_required_count
        aggregate.missing_required_count = score.missing_required_count
        aggregate.scoring_version = score.scoring_version
        semantic_similarity, reranker_score, requirements_match = self._compute_aggregate_scores(
            application.id, score
        )
        aggregate.semantic_similarity = semantic_similarity
        aggregate.reranker_score = reranker_score
        aggregate.requirements_match = requirements_match
        aggregate.model_versions_json = {
            "vacancy_extractor": {
                "name": self._vacancy_extractor.model_name,
                "version": self._vacancy_extractor.model_version,
            },
            "candidate_extractor": {
                "name": self._candidate_extractor.model_name,
                "version": self._candidate_extractor.model_version,
            },
            "reranker": {
                "name": self._reranker.model_name,
                "revision": self._reranker.model_revision,
            },
            "claim_pipeline": self._claim_pipeline is not None,
            "matching_source_version": VACANCY_MATCHING_SOURCE_VERSION,
        }
        explanation: dict[str, object] = {
            "summary": list(score.explanation),
            "matching_source_version": VACANCY_MATCHING_SOURCE_VERSION,
            "key_skill_coverage": self._build_key_skill_coverage(
                application,
                assessments,
            ),
        }
        if relevance is not None:
            explanation["hard_gate"] = {
                "decision": relevance.decision.value,
                "reason_codes": [reason.value for reason in relevance.reasons],
                "rejected_requirement_ids": list(relevance.rejected_requirement_ids),
            }
        if assessments:
            matched_ids = [
                a.requirement_id
                for a in assessments
                if a.match_level
                not in {
                    MatchLevel.MISSING,
                    MatchLevel.BLOCKER,
                    MatchLevel.INSUFFICIENT_EVIDENCE,
                    MatchLevel.EVALUATION_ERROR,
                    MatchLevel.UNRESOLVED_BLOCKER,
                }
            ]
            missing_ids = [
                a.requirement_id for a in assessments if a.match_level is MatchLevel.MISSING
            ]
            blocker_ids = [
                a.requirement_id
                for a in assessments
                if a.is_blocker and a.match_level in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            ]
            insufficient_ids = [
                a.requirement_id
                for a in assessments
                if a.match_level is MatchLevel.INSUFFICIENT_EVIDENCE
            ]
            error_ids = [
                a.requirement_id
                for a in assessments
                if a.match_level is MatchLevel.EVALUATION_ERROR
            ]
            if matched_ids:
                explanation["matched_requirement_ids"] = matched_ids
            if missing_ids:
                explanation["missing_requirement_ids"] = missing_ids
            if blocker_ids:
                explanation["blocker_requirement_ids"] = blocker_ids
            if insufficient_ids:
                explanation["insufficient_evidence_requirement_ids"] = insufficient_ids
            if error_ids:
                explanation["evaluation_error_requirement_ids"] = error_ids
        if rag_context:
            explanation["rag_context"] = rag_context
        if gap_analysis:
            explanation["gap_analysis"] = gap_analysis
        # New fields
        explanation["required_score"] = score.required_score
        explanation["preferred_score"] = score.preferred_score
        explanation["bonus_score"] = score.bonus_score
        explanation["hard_blockers"] = list(score.hard_blockers)
        explanation["hard_blockers_unresolved"] = list(score.hard_blockers_unresolved)
        explanation["confidence"] = score.confidence
        explanation["raw_score_before_blockers"] = score.raw_score_before_blockers
        explanation["blocker_penalty"] = score.blocker_penalty
        explanation["calibration_version"] = score.calibration_version
        explanation["recommendations"] = list(score.recommendations)
        aggregate.explanation_json = explanation
        aggregate.calculated_at = datetime.now(UTC)
        # Persist new scoring fields
        aggregate.required_score = score.required_score
        aggregate.preferred_score = score.preferred_score
        aggregate.bonus_score = score.bonus_score
        aggregate.confidence = score.confidence
        return aggregate

    def _build_key_skill_coverage(
        self,
        application: ApplicationRow,
        assessments: tuple[RequirementAssessment, ...],
    ) -> list[dict[str, object]]:
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            return []
        assessment_by_id = {item.requirement_id: item for item in assessments}
        requirement_rows = tuple(
            self._session.scalars(
                select(VacancyRequirementRow).where(
                    VacancyRequirementRow.vacancy_id == vacancy.id,
                    VacancyRequirementRow.id.in_(tuple(assessment_by_id)),
                )
            )
        )
        coverage: list[dict[str, object]] = []
        for skill, importance, _section in self._structured_vacancy_skills(vacancy):
            normalized_skill = self._skill_normalizer.normalize(skill).canonical
            requirement = next(
                (
                    row
                    for row in requirement_rows
                    if self._contains_normalized_skill(row.normalized_text, normalized_skill)
                ),
                None,
            )
            assessment = assessment_by_id.get(requirement.id) if requirement is not None else None
            coverage.append(
                {
                    "skill": skill,
                    "importance": importance.value,
                    "requirement_id": requirement.id if requirement is not None else None,
                    "requirement_text": (
                        requirement.requirement_text if requirement is not None else None
                    ),
                    "match_level": (
                        assessment.match_level.value if assessment is not None else "not_evaluated"
                    ),
                    "evaluated": assessment is not None,
                }
            )
        return coverage

    def _compute_aggregate_scores(
        self,
        application_id: str,
        score: DeterministicScore,
    ) -> tuple[float, float, float]:
        rows = self._session.execute(
            select(
                RequirementMatchRow.hybrid_score,
                RequirementMatchRow.reranker_score,
                VacancyRequirementRow.weight,
            )
            .join(
                VacancyRequirementRow,
                VacancyRequirementRow.id == RequirementMatchRow.requirement_id,
            )
            .where(RequirementMatchRow.application_id == application_id)
        ).all()
        total_weight = sum(row[2] for row in rows)
        if total_weight == 0:
            return (0.0, 0.0, 0.0)
        semantic_similarity = sum((row[0] or 0.0) * row[2] for row in rows) / total_weight
        reranker_scores = [(row[1] or 0.0) * row[2] for row in rows if row[1] is not None]
        reranker_weights = [row[2] for row in rows if row[1] is not None]
        reranker_score = sum(reranker_scores) / sum(reranker_weights) if reranker_weights else 0.0
        total_required = score.matched_required_count + score.missing_required_count
        requirements_match = (
            (score.matched_required_count / total_required * 100) if total_required > 0 else 100.0
        )
        return (
            round(semantic_similarity, 4),
            round(reranker_score, 4),
            round(requirements_match, 1),
        )

    async def _fetch_rag_context(
        self,
        vacancy: VacancyRow,
        application: ApplicationRow,
    ) -> dict[str, object] | None:
        if self._rag_client is None:
            return None
        query = f"{vacancy.title} {vacancy.company} {vacancy.location}".strip()
        if not query:
            return None
        try:
            response = await self._rag_client.search(
                query,
                owner_user_id=application.user_id,
                collections=("vacancies", "profiles"),
                top_k=3,
            )
            return {
                "request_id": response.request_id,
                "result_count": len(response.results),
                "effective_mode": response.effective_mode,
                "degraded": response.degraded,
                "sources": [
                    {
                        "document_id": r.document_id,
                        "collection": r.collection,
                        "score": round(r.score, 4),
                        "reranker_score": (
                            round(r.reranker_score, 4) if r.reranker_score is not None else None
                        ),
                    }
                    for r in response.results[:5]
                ],
            }
        except Exception as error:
            logger.warning(
                "rag_context_fetch_failed",
                vacancy_id=vacancy.id,
                application_id=application.id,
                error_type=type(error).__name__,
            )
            return {"error": f"{type(error).__name__}: unavailable"}

    @staticmethod
    def _classify_match(
        normalized_score: float,
        evidence: CandidateEvidenceRow | None,
        *,
        is_blocker: bool,
    ) -> MatchLevel:
        if evidence is None or normalized_score < 0.2:
            return MatchLevel.BLOCKER if is_blocker else MatchLevel.MISSING
        if evidence.experience_level in {
            ExperienceLevel.THEORETICAL.value,
            ExperienceLevel.CONCEPTUAL.value,
        }:
            return MatchLevel.THEORETICAL_ONLY
        if normalized_score >= 0.85:
            return MatchLevel.EXACT
        if normalized_score >= 0.7:
            return MatchLevel.STRONG
        if normalized_score >= 0.45:
            return MatchLevel.PARTIAL
        return MatchLevel.RELATED

    @staticmethod
    def _requirement_explanation(
        match_level: MatchLevel,
        evidence: CandidateEvidenceRow | None,
    ) -> str:
        if evidence is None:
            return "No source-grounded candidate evidence was retrieved"
        return (
            f"Evidence {evidence.id} classified as {match_level.value}; "
            f"experience level is {evidence.experience_level}"
        )

    @staticmethod
    def _extraction_run_id(
        *,
        entity_type: str,
        entity_id: str,
        source_text: str,
        model_name: str,
        model_version: str,
        schema_version: str,
    ) -> str:
        source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        identity = ":".join(
            (entity_type, entity_id, model_name, model_version, schema_version, source_hash)
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
