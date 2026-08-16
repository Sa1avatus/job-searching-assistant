from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.matching.extraction import RequirementImportance, RequirementType


class RoleFamily(StrEnum):
    SOFTWARE_ENGINEERING = "software_engineering"
    DATA_AI = "data_ai"
    PRODUCT_DESIGN = "product_design"
    CULINARY_HOSPITALITY = "culinary_hospitality"
    HEALTHCARE = "healthcare"
    FINANCE_ACCOUNTING = "finance_accounting"
    SALES_RETAIL = "sales_retail"


class RelevanceDecision(StrEnum):
    PASS = "pass"
    REVIEW = "review"
    REJECT = "reject"


class RelevanceReason(StrEnum):
    ROLE_FAMILY_MISMATCH = "ROLE_FAMILY_MISMATCH"
    NON_TECHNICAL_ROLE = "NON_TECHNICAL_ROLE"
    SENIORITY_MISMATCH = "SENIORITY_MISMATCH"
    MANDATORY_REQUIREMENT_MISMATCH = "MANDATORY_REQUIREMENT_MISMATCH"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    WORK_AUTHORIZATION_MISMATCH = "WORK_AUTHORIZATION_MISMATCH"
    LANGUAGE_MISMATCH = "LANGUAGE_MISMATCH"


@dataclass(frozen=True, slots=True)
class RelevanceRequirement:
    requirement_id: str
    requirement_type: RequirementType
    importance: RequirementImportance
    canonical_value: str
    is_blocker: bool = False


@dataclass(frozen=True, slots=True)
class RelevanceEvidence:
    requirement_type: RequirementType
    canonical_value: str


@dataclass(frozen=True, slots=True)
class RelevanceResult:
    decision: RelevanceDecision
    reasons: tuple[RelevanceReason, ...]
    rejected_requirement_ids: tuple[str, ...]


class HardRelevanceGate:
    _NON_TECHNICAL_ROLE_FAMILIES = {
        RoleFamily.CULINARY_HOSPITALITY,
        RoleFamily.HEALTHCARE,
        RoleFamily.FINANCE_ACCOUNTING,
        RoleFamily.SALES_RETAIL,
    }
    _TECHNICAL_ROLE_FAMILIES = {
        RoleFamily.SOFTWARE_ENGINEERING,
        RoleFamily.DATA_AI,
    }
    _EXPLICIT_CONTRADICTIONS = {
        RequirementType.WORK_AUTHORIZATION: {
            ("authorized_us", "requires_sponsorship_us"),
            ("no_sponsorship", "requires_sponsorship"),
        },
    }

    def evaluate(
        self,
        requirements: tuple[RelevanceRequirement, ...],
        evidence: tuple[RelevanceEvidence, ...],
    ) -> RelevanceResult:
        required_requirements = tuple(
            requirement
            for requirement in requirements
            if requirement.importance is RequirementImportance.REQUIRED
        )
        if not required_requirements:
            return RelevanceResult(
                decision=RelevanceDecision.REVIEW,
                reasons=(),
                rejected_requirement_ids=(),
            )
        evidence_by_type: dict[RequirementType, set[str]] = {}
        for item in evidence:
            value = self._normalize(item.canonical_value)
            if value:
                evidence_by_type.setdefault(item.requirement_type, set()).add(value)

        reasons: list[RelevanceReason] = []
        rejected_ids: list[str] = []
        unresolved = False
        for requirement in required_requirements:
            required_value = self._normalize(requirement.canonical_value)
            candidate_values = evidence_by_type.get(requirement.requirement_type, set())
            if not required_value or not candidate_values:
                unresolved = True
                continue
            if required_value in candidate_values:
                continue
            reason = self._reason(requirement, candidate_values)
            if reason is None:
                unresolved = True
                continue
            reasons.append(reason)
            if requirement.is_blocker:
                reasons.append(RelevanceReason.MANDATORY_REQUIREMENT_MISMATCH)
            rejected_ids.append(requirement.requirement_id)

        unique_reasons = tuple(dict.fromkeys(reasons))
        if rejected_ids:
            return RelevanceResult(
                decision=RelevanceDecision.REJECT,
                reasons=unique_reasons,
                rejected_requirement_ids=tuple(rejected_ids),
            )
        return RelevanceResult(
            decision=RelevanceDecision.REVIEW if unresolved else RelevanceDecision.PASS,
            reasons=(),
            rejected_requirement_ids=(),
        )

    def _reason(
        self,
        requirement: RelevanceRequirement,
        candidate_values: set[str],
    ) -> RelevanceReason | None:
        if requirement.requirement_type is RequirementType.ROLE:
            try:
                required_family = RoleFamily(self._normalize(requirement.canonical_value))
                candidate_families = {RoleFamily(value) for value in candidate_values}
            except ValueError:
                return None
            if (
                required_family in self._NON_TECHNICAL_ROLE_FAMILIES
                and candidate_families <= self._TECHNICAL_ROLE_FAMILIES
            ):
                return RelevanceReason.NON_TECHNICAL_ROLE
            return RelevanceReason.ROLE_FAMILY_MISMATCH
        contradictions = self._EXPLICIT_CONTRADICTIONS.get(requirement.requirement_type, set())
        required_value = self._normalize(requirement.canonical_value)
        if any((required_value, value) in contradictions for value in candidate_values):
            return RelevanceReason.WORK_AUTHORIZATION_MISMATCH
        return None

    @staticmethod
    def _normalize(value: str) -> str:
        return "_".join(value.casefold().replace("-", " ").split())
