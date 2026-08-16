from app.matching.extraction import RequirementImportance, RequirementType
from app.matching.relevance import (
    HardRelevanceGate,
    RelevanceDecision,
    RelevanceEvidence,
    RelevanceReason,
    RelevanceRequirement,
)


def _role_requirement(value: str) -> RelevanceRequirement:
    return RelevanceRequirement(
        requirement_id="role-1",
        requirement_type=RequirementType.ROLE,
        importance=RequirementImportance.REQUIRED,
        canonical_value=value,
        is_blocker=True,
    )


def test_hard_gate_rejects_sous_chef_for_technical_candidate() -> None:
    result = HardRelevanceGate().evaluate(
        (_role_requirement("culinary_hospitality"),),
        (RelevanceEvidence(RequirementType.ROLE, "software_engineering"),),
    )

    assert result.decision is RelevanceDecision.REJECT
    assert RelevanceReason.NON_TECHNICAL_ROLE in result.reasons
    assert RelevanceReason.MANDATORY_REQUIREMENT_MISMATCH in result.reasons
    assert result.rejected_requirement_ids == ("role-1",)


def test_hard_gate_passes_matching_canonical_role_family() -> None:
    result = HardRelevanceGate().evaluate(
        (_role_requirement("data_ai"),),
        (RelevanceEvidence(RequirementType.ROLE, "data-ai"),),
    )

    assert result.decision is RelevanceDecision.PASS
    assert not result.reasons


def test_hard_gate_requires_review_when_role_evidence_is_unknown() -> None:
    result = HardRelevanceGate().evaluate((_role_requirement("data_ai"),), ())

    assert result.decision is RelevanceDecision.REVIEW
    assert not result.rejected_requirement_ids


def test_hard_gate_requires_review_without_typed_requirements() -> None:
    result = HardRelevanceGate().evaluate((), ())

    assert result.decision is RelevanceDecision.REVIEW


def test_hard_gate_rejects_explicit_work_authorization_contradiction() -> None:
    requirement = RelevanceRequirement(
        requirement_id="authorization-1",
        requirement_type=RequirementType.WORK_AUTHORIZATION,
        importance=RequirementImportance.REQUIRED,
        canonical_value="authorized_us",
        is_blocker=True,
    )
    result = HardRelevanceGate().evaluate(
        (requirement,),
        (RelevanceEvidence(RequirementType.WORK_AUTHORIZATION, "requires_sponsorship_us"),),
    )

    assert result.decision is RelevanceDecision.REJECT
    assert RelevanceReason.WORK_AUTHORIZATION_MISMATCH in result.reasons
