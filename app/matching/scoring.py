from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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
    MISSING = "missing"
    BLOCKER = "blocker"


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    REVIEW = "review"
    INELIGIBLE = "ineligible"


_MATCH_FACTOR = {
    MatchLevel.EXACT: 1.0,
    MatchLevel.STRONG: 0.9,
    MatchLevel.PARTIAL: 0.65,
    MatchLevel.RELATED: 0.35,
    MatchLevel.THEORETICAL_ONLY: 0.15,
    MatchLevel.MISSING: 0.0,
    MatchLevel.BLOCKER: 0.0,
}

_IMPORTANCE_FACTOR = {
    RequirementImportance.REQUIRED: 1.0,
    RequirementImportance.PREFERRED: 0.5,
    RequirementImportance.OPTIONAL: 0.2,
    RequirementImportance.UNKNOWN: 0.4,
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


class DeterministicMatchScorer:
    scoring_version = "matching-v2.1"

    def score(self, assessments: tuple[RequirementAssessment, ...]) -> DeterministicScore:
        effective_level_by_requirement = {
            assessment.requirement_id: self._effective_match_level(assessment)
            for assessment in assessments
        }
        blocker_assessments = [
            assessment
            for assessment in assessments
            if (
                assessment.is_blocker
                and effective_level_by_requirement[assessment.requirement_id]
                in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            )
        ]
        missing_authorization = any(
            assessment.requirement_type is RequirementType.WORK_AUTHORIZATION
            and assessment.importance is RequirementImportance.REQUIRED
            and effective_level_by_requirement[assessment.requirement_id]
            in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            for assessment in assessments
        )
        required_assessments = [
            assessment
            for assessment in assessments
            if assessment.importance is RequirementImportance.REQUIRED
        ]
        matched_required_count = sum(
            effective_level_by_requirement[assessment.requirement_id]
            not in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            for assessment in required_assessments
        )
        missing_required_count = len(required_assessments) - matched_required_count

        weighted_score = self._weighted_score(assessments, effective_level_by_requirement)
        final_score = round(weighted_score)
        explanation: list[str] = []
        if blocker_assessments:
            final_score = min(final_score, 20)
            explanation.append(f"{len(blocker_assessments)} hard blocker(s) are not satisfied")
        if missing_authorization:
            final_score = min(final_score, 20)
            explanation.append("Required work authorization is missing")
        elif missing_required_count:
            final_score = min(final_score, 49)
            explanation.append(f"{missing_required_count} required requirement(s) are missing")

        eligibility_status = (
            EligibilityStatus.INELIGIBLE
            if blocker_assessments or missing_authorization
            else EligibilityStatus.REVIEW
            if missing_required_count
            else EligibilityStatus.ELIGIBLE
        )
        if not explanation:
            explanation.append("All required requirements have supporting evidence")

        return DeterministicScore(
            eligibility_status=eligibility_status,
            final_score=max(0, min(100, final_score)),
            hard_skill_score=self._component_score(
                assessments,
                effective_level_by_requirement,
                RequirementType.HARD_SKILL,
                RequirementImportance.REQUIRED,
            ),
            preferred_skill_score=self._component_score(
                assessments,
                effective_level_by_requirement,
                RequirementType.HARD_SKILL,
                RequirementImportance.PREFERRED,
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
            blocker_count=len(blocker_assessments),
            matched_required_count=matched_required_count,
            missing_required_count=missing_required_count,
            scoring_version=self.scoring_version,
            explanation=tuple(explanation),
        )

    @staticmethod
    def _effective_match_level(assessment: RequirementAssessment) -> MatchLevel:
        if (
            assessment.requirement_type
            in {RequirementType.HARD_SKILL, RequirementType.EXPERIENCE}
            and assessment.evidence_experience_level
            in {ExperienceLevel.THEORETICAL, ExperienceLevel.CONCEPTUAL}
            and assessment.match_level
            in {MatchLevel.EXACT, MatchLevel.STRONG, MatchLevel.PARTIAL}
        ):
            return MatchLevel.THEORETICAL_ONLY
        return assessment.match_level

    @staticmethod
    def _weighted_score(
        assessments: tuple[RequirementAssessment, ...],
        effective_level_by_requirement: dict[str, MatchLevel],
    ) -> float:
        total_weight = sum(
            assessment.weight * _IMPORTANCE_FACTOR[assessment.importance]
            for assessment in assessments
        )
        if total_weight == 0:
            return 0.0
        earned_weight = sum(
            assessment.weight
            * _IMPORTANCE_FACTOR[assessment.importance]
            * _MATCH_FACTOR[effective_level_by_requirement[assessment.requirement_id]]
            for assessment in assessments
        )
        return 100 * earned_weight / total_weight

    @staticmethod
    def _component_score(
        assessments: tuple[RequirementAssessment, ...],
        effective_level_by_requirement: dict[str, MatchLevel],
        requirement_type: RequirementType,
        importance: RequirementImportance | None = None,
    ) -> int:
        component = [
            assessment
            for assessment in assessments
            if assessment.requirement_type is requirement_type
            and (importance is None or assessment.importance is importance)
        ]
        total_weight = sum(assessment.weight for assessment in component)
        if total_weight == 0:
            return 0
        earned_weight = sum(
            assessment.weight
            * _MATCH_FACTOR[effective_level_by_requirement[assessment.requirement_id]]
            for assessment in component
        )
        return round(100 * earned_weight / total_weight)

