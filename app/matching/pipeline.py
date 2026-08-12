from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import Protocol

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.matching.extraction import (
    CandidateEvidenceExtractor,
    EvidenceType,
    ExperienceLevel,
    RequirementImportance,
    RequirementType,
    VacancyRequirementExtractor,
)
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
        shadow_mode: bool = True,
        fallback_enabled: bool = True,
    ) -> None:
        self._session = session
        self._vacancy_extractor = vacancy_extractor
        self._candidate_extractor = candidate_extractor
        self._retriever = retriever
        self._reranker = reranker
        self._scorer = scorer
        self._evidence_indexer = evidence_indexer
        self._relevance_gate = relevance_gate or HardRelevanceGate()
        self._shadow_mode = shadow_mode
        self._fallback_enabled = fallback_enabled

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
            self._set_status(application.id, "retrieving")
            self._set_status(application.id, "reranking")
            assessments = []
            for requirement in requirement_rows:
                assessment = await self._match_requirement(application, cv_file, requirement)
                assessments.append(assessment)
            deterministic_score = self._scorer.score(tuple(assessments))
            self._session.flush()
            aggregate = self._store_aggregate(
                application,
                cv_file,
                deterministic_score,
                relevance=relevance,
                assessments=tuple(assessments),
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
        self._session.commit()

    def _set_status(self, application_id: str, status: str) -> None:
        aggregate = self._session.get(ApplicationMatchResultRow, application_id)
        if aggregate is None:
            raise MatchingEntityNotFoundError("Matching run state not found")
        aggregate.status = status
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
        rows = tuple(
            VacancyRequirementRow(
                vacancy_id=vacancy.id,
                requirement_text=requirement.text,
                normalized_text=requirement.normalized_text,
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
        )
        self._session.add_all(rows)
        return rows

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
                skill_name=evidence.skill_name,
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
                match_level=(MatchLevel.BLOCKER if row.id in rejected_ids else MatchLevel.MISSING),
            )
            for row in requirements
        )
        return self._store_aggregate(
            application,
            cv_file,
            self._scorer.score(assessments),
            relevance=relevance,
        )

    async def _match_requirement(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        requirement: VacancyRequirementRow,
    ) -> RequirementAssessment:
        retrieval_started = time.perf_counter()
        candidates = await self._retriever.retrieve(
            requirement.normalized_text,
            user_id=application.user_id,
            cv_file_id=cv_file.id,
        )
        metrics.observe(
            "retrieval_duration_seconds",
            time.perf_counter() - retrieval_started,
        )
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
        metrics.observe(
            "reranker_duration_seconds",
            time.perf_counter() - reranker_started,
        )
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
                    "reranker": {
                        "name": self._reranker.model_name,
                        "revision": self._reranker.model_revision,
                        "status": ("available" if reranker_available else "reranker_unavailable"),
                    }
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

    def _store_aggregate(
        self,
        application: ApplicationRow,
        cv_file: CvFileRow,
        score: DeterministicScore,
        *,
        relevance: RelevanceResult | None = None,
        assessments: tuple[RequirementAssessment, ...] = (),
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
        semantic_similarity, reranker_score, requirements_match = (
            self._compute_aggregate_scores(application.id, score)
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
        }
        explanation: dict[str, object] = {
            "summary": list(score.explanation),
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
                if a.match_level not in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            ]
            missing_ids = [
                a.requirement_id
                for a in assessments
                if a.match_level is MatchLevel.MISSING
            ]
            blocker_ids = [
                a.requirement_id
                for a in assessments
                if a.is_blocker
                and a.match_level in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            ]
            if matched_ids:
                explanation["matched_requirement_ids"] = matched_ids
            if missing_ids:
                explanation["missing_requirement_ids"] = missing_ids
            if blocker_ids:
                explanation["blocker_requirement_ids"] = blocker_ids
        aggregate.explanation_json = explanation
        aggregate.calculated_at = datetime.now(UTC)
        return aggregate

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
        semantic_similarity = (
            sum((row[0] or 0.0) * row[2] for row in rows) / total_weight
        )
        reranker_scores = [(row[1] or 0.0) * row[2] for row in rows if row[1] is not None]
        reranker_weights = [row[2] for row in rows if row[1] is not None]
        reranker_score = (
            sum(reranker_scores) / sum(reranker_weights) if reranker_weights else 0.0
        )
        total_required = score.matched_required_count + score.missing_required_count
        requirements_match = (
            (score.matched_required_count / total_required * 100)
            if total_required > 0
            else 100.0
        )
        return (
            round(semantic_similarity, 4),
            round(reranker_score, 4),
            round(requirements_match, 1),
        )

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
            (
                entity_type,
                entity_id,
                model_name,
                model_version,
                schema_version,
                source_hash,
            )
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
