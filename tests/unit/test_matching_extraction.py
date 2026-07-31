import asyncio

import pytest
from pydantic import ValidationError

from app.matching.extraction import (
    CandidateEvidence,
    CandidateEvidenceExtraction,
    EvidenceType,
    ExperienceLevel,
    FakeCandidateEvidenceExtractor,
    FakeVacancyRequirementExtractor,
    RequirementImportance,
    RequirementType,
    VacancyExtraction,
    VacancyRequirement,
)


def _vacancy_extraction() -> VacancyExtraction:
    return VacancyExtraction(
        role="Platform Engineer",
        seniority="senior",
        responsibilities=["Operate production services"],
        requirements=[
            VacancyRequirement(
                text="Production experience with Kubernetes",
                normalized_text="kubernetes production experience",
                requirement_type=RequirementType.HARD_SKILL,
                importance=RequirementImportance.REQUIRED,
                is_blocker=False,
                alternatives=["K8s", " k8s ", "OpenShift"],
                source_fragment="You have production experience with Kubernetes.",
                source_section="Requirements",
                confidence=0.98,
            )
        ],
        constraints=[],
        hard_blockers=[],
        preferred_items=["PostgreSQL"],
        confidence=0.96,
    )


def _candidate_extraction() -> CandidateEvidenceExtraction:
    return CandidateEvidenceExtraction(
        evidence=[
            CandidateEvidence(
                text="Built a Kubernetes deployment as a study project",
                normalized_text="kubernetes study project",
                evidence_type=EvidenceType.PROJECT_EXPERIENCE,
                skill_name="Kubernetes",
                experience_level=ExperienceLevel.PROJECT,
                is_verified=True,
                source_fragment="Built a Kubernetes deployment as a study project.",
                source_section="Projects",
                confidence=0.95,
            )
        ],
        confidence=0.95,
    )


def test_vacancy_extraction_normalizes_requirement_alternatives() -> None:
    extraction = _vacancy_extraction()

    assert extraction.requirements[0].alternatives == ["K8s", "OpenShift"]


def test_candidate_evidence_preserves_non_production_experience_level() -> None:
    extraction = _candidate_extraction()

    assert extraction.evidence[0].experience_level is ExperienceLevel.PROJECT
    assert extraction.evidence[0].source_fragment.startswith("Built a Kubernetes")


def test_extraction_models_reject_unknown_or_invalid_fields() -> None:
    with pytest.raises(ValidationError):
        VacancyRequirement.model_validate(
            {
                "text": "Python",
                "normalized_text": "python",
                "requirement_type": "hard_skill",
                "confidence": 1.2,
                "source_fragment": "Python",
                "invented_field": True,
            }
        )


def test_fake_extractors_are_deterministic_and_return_independent_copies() -> None:
    async def run() -> None:
        vacancy_extractor = FakeVacancyRequirementExtractor({"vacancy-1": _vacancy_extraction()})
        candidate_extractor = FakeCandidateEvidenceExtractor({"cv-1": _candidate_extraction()})

        first = await vacancy_extractor.extract(vacancy_id="vacancy-1", source_text="ignored")
        second = await vacancy_extractor.extract(vacancy_id="vacancy-1", source_text="different")
        candidate = await candidate_extractor.extract(
            user_id="user-1",
            cv_file_id="cv-1",
            source_text="ignored",
            source_is_verified=True,
        )

        assert first == second
        assert first is not second
        assert candidate == _candidate_extraction()

    asyncio.run(run())


def test_fake_extractors_fail_clearly_for_missing_fixture() -> None:
    async def run() -> None:
        extractor = FakeVacancyRequirementExtractor({})
        with pytest.raises(LookupError, match="vacancy-missing"):
            await extractor.extract(vacancy_id="vacancy-missing", source_text="text")

    asyncio.run(run())
