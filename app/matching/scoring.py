from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.matching.entailment import EntailmentRelation
from app.matching.extraction import (
    ExperienceLevel,
    RequirementImportance,
    RequirementType,
)


class MatchLevel(StrEnum):
    EXACT = "exact"
    STRONG = "strong"
    PARTIAL = "partial"
    RELATED = "related"
    THEORETICAL_ONLY = "theoretical_only"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EVALUATION_ERROR = "evaluation_error"
    MISSING = "missing"
    BLOCKER = "blocker"
    UNRESOLVED_BLOCKER = "unresolved_blocker"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    REVIEW = "review"
    INELIGIBLE = "ineligible"
    NEEDS_CONFIRMATION = "needs_confirmation"


_MATCH_FACTOR = {
    MatchLevel.EXACT: 1.0,
    MatchLevel.STRONG: 0.9,
    MatchLevel.PARTIAL: 0.65,
    MatchLevel.RELATED: 0.35,
    MatchLevel.THEORETICAL_ONLY: 0.15,
    MatchLevel.INSUFFICIENT_EVIDENCE: 0.15,
    MatchLevel.EVALUATION_ERROR: 0.10,
    MatchLevel.MISSING: 0.0,
    MatchLevel.BLOCKER: 0.0,
    MatchLevel.UNRESOLVED_BLOCKER: 0.0,
}

_IMPORTANCE_FACTOR = {
    RequirementImportance.REQUIRED: 1.0,
    RequirementImportance.PREFERRED: 0.5,
    RequirementImportance.OPTIONAL: 0.2,
    RequirementImportance.UNKNOWN: 0.4,
}

_ENTAILMENT_TO_MATCH_FACTOR = {
    EntailmentRelation.ENTAILED: 1.0,
    EntailmentRelation.PARTIAL: 0.65,
    EntailmentRelation.RELATED_BUT_INSUFFICIENT: 0.15,
    EntailmentRelation.INSUFFICIENT_EVIDENCE: 0.15,
    EntailmentRelation.CONTRADICTED: 0.0,
    EntailmentRelation.EVALUATION_ERROR: 0.10,
    EntailmentRelation.UNKNOWN: 0.0,
}

_CATEGORY_WEIGHTS = {
    "required": 0.60,
    "preferred": 0.25,
    "bonus": 0.15,
}


@dataclass(frozen=True, slots=True)
class RequirementAssessment:
    requirement_id: str
    requirement_type: RequirementType
    importance: RequirementImportance
    weight: float
    is_blocker: bool
    match_level: MatchLevel
    evidence_experience_level: ExperienceLevel | None = None
    entailment_relation: EntailmentRelation | None = None
    evidence_strength: float | None = None
    is_hard_blocker: bool = False
    is_unresolved_blocker: bool = False

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError("weight must be nonnegative")


@dataclass(frozen=True, slots=True)
class DeterministicScore:
    eligibility_status: EligibilityStatus
    final_score: int
    hard_skill_score: int
    preferred_skill_score: int
    role_score: int
    seniority_score: int
    experience_score: int
    work_format_score: int
    location_score: int
    domain_score: int
    language_score: int
    blocker_count: int
    matched_required_count: int
    missing_required_count: int
    scoring_version: str
    explanation: tuple[str, ...]
    required_score: int = 0
    preferred_score: int = 0
    bonus_score: int = 0
    hard_blockers: tuple[str, ...] = ()
    hard_blockers_unresolved: tuple[str, ...] = ()
    confidence: float = 0.0


class DeterministicMatchScorer:
    scoring_version = "matching-v2.3"

    def score(self, assessments: tuple[RequirementAssessment, ...]) -> DeterministicScore:
        effective_level_by_requirement = {
            assessment.requirement_id: self._effective_match_level(assessment)
            for assessment in assessments
        }

        # Confirmed hard blockers: hard_blocker AND confirmed missing/contradicted
        confirmed_hard_blockers = [
            a for a in assessments
            if a.is_hard_blocker
            and effective_level_by_requirement[a.requirement_id]
            in {MatchLevel.MISSING, MatchLevel.BLOCKER}
        ]
        # Unresolved hard blockers: marked as unresolved OR hard_blocker with uncertain state
        unresolved_hard_blockers = [
            a for a in assessments
            if (a.is_hard_blocker or a.is_unresolved_blocker)
            and effective_level_by_requirement[a.requirement_id]
            in {
                MatchLevel.INSUFFICIENT_EVIDENCE, MatchLevel.EVALUATION_ERROR,
                MatchLevel.UNRESOLVED_BLOCKER,
            }
        ]

        blocker_assessments = [
            a for a in assessments
            if a.is_blocker
            and not a.is_hard_blocker
            and effective_level_by_requirement[a.requirement_id]
            in {MatchLevel.MISSING, MatchLevel.BLOCKER}
        ]

        missing_authorization = any(
            a.requirement_type is RequirementType.WORK_AUTHORIZATION
            and a.importance is RequirementImportance.REQUIRED
            and effective_level_by_requirement[a.requirement_id]
            in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            for a in assessments
        )

        required_assessments = [
            a for a in assessments
            if a.importance is RequirementImportance.REQUIRED and not a.is_hard_blocker
        ]
        matched_required_count = sum(
            effective_level_by_requirement[a.requirement_id]
            not in {
                MatchLevel.MISSING, MatchLevel.BLOCKER,
                MatchLevel.INSUFFICIENT_EVIDENCE, MatchLevel.EVALUATION_ERROR,
            }
            for a in required_assessments
        )
        missing_required_count = len(required_assessments) - matched_required_count

        required_score = self._category_score(
            assessments, effective_level_by_requirement, RequirementImportance.REQUIRED
        )
        preferred_score = self._category_score(
            assessments, effective_level_by_requirement, RequirementImportance.PREFERRED
        )
        bonus_score = self._category_score(
            assessments, effective_level_by_requirement, RequirementImportance.OPTIONAL
        )

        weighted_score = self._weighted_score(assessments, effective_level_by_requirement)

        has_confirmed_hard_blockers = bool(confirmed_hard_blockers) or missing_authorization

        final_score = round(weighted_score)

        explanation: list[str] = []
        if confirmed_hard_blockers:
            explanation.append(
                f"{len(confirmed_hard_blockers)} hard blocker(s) confirmed not satisfied"
            )
        if unresolved_hard_blockers:
            explanation.append(
                f"{len(unresolved_hard_blockers)} hard blocker(s) could not be verified "
                "(insufficient evidence)"
            )
        if blocker_assessments:
            explanation.append(f"{len(blocker_assessments)} blocker(s) are not satisfied")
        if missing_authorization:
            explanation.append("Required work authorization is missing")
        if missing_required_count:
            explanation.append(f"{missing_required_count} required requirement(s) are missing")

        # Score caps — only CONFIRMED hard blockers zero the score
        if has_confirmed_hard_blockers:
            final_score = 0
        elif blocker_assessments:
            final_score = min(final_score, 20)
        elif missing_required_count:
            if required_assessments:
                missing_ratio = missing_required_count / len(required_assessments)
                cap = max(20, round(80 * (1 - missing_ratio)))
                final_score = min(final_score, cap)
            else:
                final_score = min(final_score, 49)

        # Eligibility
        if has_confirmed_hard_blockers:
            eligibility_status = EligibilityStatus.INELIGIBLE
        elif unresolved_hard_blockers:
            eligibility_status = EligibilityStatus.NEEDS_CONFIRMATION
        elif blocker_assessments or missing_required_count:
            eligibility_status = EligibilityStatus.REVIEW
        else:
            eligibility_status = EligibilityStatus.ELIGIBLE

        if not explanation:
            explanation.append("All required requirements have supporting evidence")

        # Confidence: average of all evidence strengths, penalized by error states
        strengths = [a.evidence_strength for a in assessments if a.evidence_strength is not None]
        base_confidence = sum(strengths) / len(strengths) if strengths else 0.0
        error_count = sum(
            1 for a in assessments
            if a.entailment_relation in {
                EntailmentRelation.EVALUATION_ERROR, EntailmentRelation.UNKNOWN
            }
        )
        error_penalty = min(0.3, error_count * 0.05)
        confidence = max(0.0, base_confidence - error_penalty)

        confirmed_blocker_ids = tuple(a.requirement_id for a in confirmed_hard_blockers)
        unresolved_blocker_ids = tuple(a.requirement_id for a in unresolved_hard_blockers)

        return DeterministicScore(
            eligibility_status=eligibility_status,
            final_score=max(0, min(100, final_score)),
            hard_skill_score=self._component_score(
                assessments, effective_level_by_requirement,
                RequirementType.HARD_SKILL, RequirementImportance.REQUIRED,
            ),
            preferred_skill_score=self._component_score(
                assessments, effective_level_by_requirement,
                RequirementType.HARD_SKILL, RequirementImportance.PREFERRED,
            ),
            role_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.ROLE
            ),
            seniority_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.SENIORITY
            ),
            experience_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.EXPERIENCE
            ),
            work_format_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.WORK_FORMAT
            ),
            location_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.LOCATION
            ),
            domain_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.DOMAIN
            ),
            language_score=self._component_score(
                assessments, effective_level_by_requirement, RequirementType.LANGUAGE
            ),
            blocker_count=len(blocker_assessments) + len(confirmed_hard_blockers),
            matched_required_count=matched_required_count,
            missing_required_count=missing_required_count,
            scoring_version=self.scoring_version,
            explanation=tuple(explanation),
            required_score=required_score,
            preferred_score=preferred_score,
            bonus_score=bonus_score,
            hard_blockers=confirmed_blocker_ids,
            hard_blockers_unresolved=unresolved_blocker_ids,
            confidence=round(confidence, 3),
        )

    @staticmethod
    def _effective_match_level(assessment: RequirementAssessment) -> MatchLevel:
        if assessment.entailment_relation is not None:
            if assessment.entailment_relation is EntailmentRelation.ENTAILED:
                level = MatchLevel.EXACT
            elif assessment.entailment_relation is EntailmentRelation.PARTIAL:
                level = MatchLevel.PARTIAL
            elif assessment.entailment_relation is EntailmentRelation.RELATED_BUT_INSUFFICIENT:
                level = MatchLevel.RELATED
            elif assessment.entailment_relation is EntailmentRelation.INSUFFICIENT_EVIDENCE:
                level = MatchLevel.INSUFFICIENT_EVIDENCE
            elif assessment.entailment_relation is EntailmentRelation.EVALUATION_ERROR:
                level = MatchLevel.EVALUATION_ERROR
            elif assessment.entailment_relation is EntailmentRelation.CONTRADICTED:
                level = MatchLevel.MISSING
            else:
                level = MatchLevel.MISSING
            if (
                assessment.requirement_type
                in {RequirementType.HARD_SKILL, RequirementType.EXPERIENCE}
                and assessment.evidence_experience_level
                in {ExperienceLevel.THEORETICAL, ExperienceLevel.CONCEPTUAL}
                and level in {MatchLevel.EXACT, MatchLevel.STRONG, MatchLevel.PARTIAL}
            ):
                return MatchLevel.THEORETICAL_ONLY
            return level

        if (
            assessment.requirement_type in {RequirementType.HARD_SKILL, RequirementType.EXPERIENCE}
            and assessment.evidence_experience_level
            in {ExperienceLevel.THEORETICAL, ExperienceLevel.CONCEPTUAL}
            and assessment.match_level in {MatchLevel.EXACT, MatchLevel.STRONG, MatchLevel.PARTIAL}
        ):
            return MatchLevel.THEORETICAL_ONLY
        return assessment.match_level

    @staticmethod
    def _weighted_score(
        assessments: tuple[RequirementAssessment, ...],
        effective_level_by_requirement: dict[str, MatchLevel],
    ) -> float:
        total_weight = sum(a.weight * _IMPORTANCE_FACTOR[a.importance] for a in assessments)
        if total_weight == 0:
            return 0.0
        earned_weight = sum(
            a.weight
            * _IMPORTANCE_FACTOR[a.importance]
            * _MATCH_FACTOR[effective_level_by_requirement[a.requirement_id]]
            for a in assessments
        )
        return 100 * earned_weight / total_weight

    @staticmethod
    def _category_score(
        assessments: tuple[RequirementAssessment, ...],
        effective_level_by_requirement: dict[str, MatchLevel],
        importance: RequirementImportance,
    ) -> int:
        category = [a for a in assessments if a.importance is importance and not a.is_hard_blocker]
        total_weight = sum(a.weight for a in category)
        if total_weight == 0:
            return 0
        earned_weight = sum(
            a.weight * _MATCH_FACTOR[effective_level_by_requirement[a.requirement_id]]
            for a in category
        )
        return round(100 * earned_weight / total_weight)

    @staticmethod
    def _component_score(
        assessments: tuple[RequirementAssessment, ...],
        effective_level_by_requirement: dict[str, MatchLevel],
        requirement_type: RequirementType,
        importance: RequirementImportance | None = None,
    ) -> int:
        component = [
            a for a in assessments
            if a.requirement_type is requirement_type
            and (importance is None or a.importance is importance)
        ]
        total_weight = sum(a.weight for a in component)
        if total_weight == 0:
            return 0
        earned_weight = sum(
            a.weight * _MATCH_FACTOR[effective_level_by_requirement[a.requirement_id]]
            for a in component
        )
        return round(100 * earned_weight / total_weight)
