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

_ENTAILMENT_TO_MATCH_FACTOR = {
    EntailmentRelation.ENTAILED: 1.0,
    EntailmentRelation.PARTIAL: 0.65,
    EntailmentRelation.RELATED_BUT_INSUFFICIENT: 0.15,
    EntailmentRelation.CONTRADICTED: 0.0,
    EntailmentRelation.UNKNOWN: 0.0,
}

# New: separate scoring weights for each importance category
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
    # New fields for entailment-based scoring
    entailment_relation: EntailmentRelation | None = None
    evidence_strength: float | None = None
    is_hard_blocker: bool = False

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
    # New fields
    required_score: int = 0
    preferred_score: int = 0
    bonus_score: int = 0
    hard_blockers: tuple[str, ...] = ()
    confidence: float = 0.0


class DeterministicMatchScorer:
    scoring_version = "matching-v2.2"

    def score(self, assessments: tuple[RequirementAssessment, ...]) -> DeterministicScore:
        effective_level_by_requirement = {
            assessment.requirement_id: self._effective_match_level(assessment)
            for assessment in assessments
        }

        # Separate hard blockers from regular required
        hard_blocker_assessments = [
            a
            for a in assessments
            if a.is_hard_blocker
            and effective_level_by_requirement[a.requirement_id]
            in {MatchLevel.MISSING, MatchLevel.BLOCKER}
        ]
        blocker_assessments = [
            a
            for a in assessments
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
            a
            for a in assessments
            if a.importance is RequirementImportance.REQUIRED and not a.is_hard_blocker
        ]
        matched_required_count = sum(
            effective_level_by_requirement[a.requirement_id]
            not in {MatchLevel.MISSING, MatchLevel.BLOCKER}
            for a in required_assessments
        )
        missing_required_count = len(required_assessments) - matched_required_count

        # Category-specific scores
        required_score = self._category_score(
            assessments, effective_level_by_requirement, RequirementImportance.REQUIRED
        )
        preferred_score = self._category_score(
            assessments, effective_level_by_requirement, RequirementImportance.PREFERRED
        )
        bonus_score = self._category_score(
            assessments, effective_level_by_requirement, RequirementImportance.OPTIONAL
        )

        # Weighted overall score - no longer caps at 49 for missing required
        weighted_score = self._weighted_score(assessments, effective_level_by_requirement)

        # Hard blockers cap at 0 (truly ineligible)
        has_hard_blockers = bool(hard_blocker_assessments) or missing_authorization

        final_score = round(weighted_score)

        explanation: list[str] = []
        if hard_blocker_assessments:
            explanation.append(f"{len(hard_blocker_assessments)} hard blocker(s) are not satisfied")
        if blocker_assessments:
            explanation.append(f"{len(blocker_assessments)} blocker(s) are not satisfied")
        if missing_authorization:
            explanation.append("Required work authorization is missing")
        if missing_required_count:
            explanation.append(f"{missing_required_count} required requirement(s) are missing")

        # Score caps based on severity
        if has_hard_blockers:
            final_score = 0
        elif blocker_assessments:
            final_score = min(final_score, 20)
        elif missing_required_count:
            # Reduced penalty: proportional to how many required are missing
            if required_assessments:
                missing_ratio = missing_required_count / len(required_assessments)
                cap = max(20, round(80 * (1 - missing_ratio)))
                final_score = min(final_score, cap)
            else:
                final_score = min(final_score, 49)

        eligibility_status = (
            EligibilityStatus.INELIGIBLE
            if has_hard_blockers
            else EligibilityStatus.REVIEW
            if blocker_assessments or missing_required_count
            else EligibilityStatus.ELIGIBLE
        )

        if not explanation:
            explanation.append("All required requirements have supporting evidence")

        # Compute confidence from evidence strength
        strengths = [a.evidence_strength for a in assessments if a.evidence_strength is not None]
        confidence = sum(strengths) / len(strengths) if strengths else 0.0

        hard_blocker_ids = tuple(a.requirement_id for a in hard_blocker_assessments)

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
            blocker_count=len(blocker_assessments) + len(hard_blocker_assessments),
            matched_required_count=matched_required_count,
            missing_required_count=missing_required_count,
            scoring_version=self.scoring_version,
            explanation=tuple(explanation),
            required_score=required_score,
            preferred_score=preferred_score,
            bonus_score=bonus_score,
            hard_blockers=hard_blocker_ids,
            confidence=round(confidence, 3),
        )

    @staticmethod
    def _effective_match_level(assessment: RequirementAssessment) -> MatchLevel:
        # Use entailment relation if available
        if assessment.entailment_relation is not None:
            if assessment.entailment_relation is EntailmentRelation.ENTAILED:
                level = MatchLevel.EXACT
            elif assessment.entailment_relation is EntailmentRelation.PARTIAL:
                level = MatchLevel.PARTIAL
            elif assessment.entailment_relation is EntailmentRelation.RELATED_BUT_INSUFFICIENT:
                level = MatchLevel.RELATED
            else:
                level = MatchLevel.MISSING
            # Downgrade if evidence is theoretical
            if (
                assessment.requirement_type
                in {RequirementType.HARD_SKILL, RequirementType.EXPERIENCE}
                and assessment.evidence_experience_level
                in {ExperienceLevel.THEORETICAL, ExperienceLevel.CONCEPTUAL}
                and level in {MatchLevel.EXACT, MatchLevel.STRONG, MatchLevel.PARTIAL}
            ):
                return MatchLevel.THEORETICAL_ONLY
            return level

        # Legacy path: use match level with experience downgrade
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
            a
            for a in assessments
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
