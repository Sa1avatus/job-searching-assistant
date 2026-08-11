from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequirementType(StrEnum):
    HARD_SKILL = "hard_skill"
    SOFT_SKILL = "soft_skill"
    ROLE = "role"
    SENIORITY = "seniority"
    EXPERIENCE = "experience"
    LANGUAGE = "language"
    EDUCATION = "education"
    LOCATION = "location"
    WORK_FORMAT = "work_format"
    WORK_AUTHORIZATION = "work_authorization"
    COMPENSATION = "compensation"
    DOMAIN = "domain"
    RESPONSIBILITY = "responsibility"
    OTHER = "other"


class RequirementImportance(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"
    UNKNOWN = "unknown"


class ExperienceLevel(StrEnum):
    HANDS_ON = "hands_on"
    PRODUCTION = "production"
    PROJECT = "project"
    THEORETICAL = "theoretical"
    CONCEPTUAL = "conceptual"
    RELATED = "related"
    UNKNOWN = "unknown"


class EvidenceType(StrEnum):
    ROLE = "role"
    SENIORITY = "seniority"
    WORK_EXPERIENCE = "work_experience"
    PROJECT_EXPERIENCE = "project_experience"
    SKILL_STATEMENT = "skill_statement"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    AUTHORIZATION = "work_authorization"
    LOCATION = "location"
    PREFERENCE = "preference"
    OTHER = "other"


class StrictExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VacancyRequirement(StrictExtractionModel):
    text: str = Field(min_length=1, max_length=2_000)
    normalized_text: str = Field(min_length=1, max_length=1_000)
    requirement_type: RequirementType
    importance: RequirementImportance = RequirementImportance.UNKNOWN
    weight: float = Field(default=1.0, ge=0, le=100)
    is_blocker: bool = False
    alternatives: list[str] = Field(default_factory=list, max_length=20)
    source_fragment: str = Field(min_length=1, max_length=4_000)
    source_section: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0, le=1)

    @field_validator("alternatives")
    @classmethod
    def normalize_alternatives(cls, alternatives: list[str]) -> list[str]:
        normalized_alternatives: list[str] = []
        seen: set[str] = set()
        for alternative in alternatives:
            normalized = " ".join(alternative.split())
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                normalized_alternatives.append(normalized)
        return normalized_alternatives


class VacancyExtraction(StrictExtractionModel):
    role: str | None = Field(default=None, max_length=300)
    seniority: str | None = Field(default=None, max_length=100)
    responsibilities: list[str] = Field(default_factory=list, max_length=100)
    requirements: list[VacancyRequirement] = Field(default_factory=list, max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    hard_blockers: list[str] = Field(default_factory=list, max_length=100)
    preferred_items: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(ge=0, le=1)


class CandidateEvidence(StrictExtractionModel):
    text: str = Field(min_length=1, max_length=4_000)
    normalized_text: str = Field(min_length=1, max_length=2_000)
    evidence_type: EvidenceType
    skill_name: str | None = Field(default=None, max_length=200)
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    years: float | None = Field(default=None, ge=0, le=100)
    is_verified: bool = False
    source_fragment: str = Field(min_length=1, max_length=4_000)
    source_section: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0, le=1)


class CandidateEvidenceExtraction(StrictExtractionModel):
    evidence: list[CandidateEvidence] = Field(default_factory=list, max_length=500)
    confidence: float = Field(ge=0, le=1)


class VacancyRequirementExtractor(Protocol):
    model_name: str
    model_version: str
    schema_version: str

    async def extract(self, *, vacancy_id: str, source_text: str) -> VacancyExtraction: ...


class CandidateEvidenceExtractor(Protocol):
    model_name: str
    model_version: str
    schema_version: str

    async def extract(
        self,
        *,
        user_id: str,
        cv_file_id: str,
        source_text: str,
        source_is_verified: bool = False,
    ) -> CandidateEvidenceExtraction: ...


class FakeVacancyRequirementExtractor:
    model_name = "fake-vacancy-extractor"
    model_version = "1"
    schema_version = "1"

    def __init__(self, extraction_by_vacancy_id: dict[str, VacancyExtraction]) -> None:
        self._extraction_by_vacancy_id = dict(extraction_by_vacancy_id)

    async def extract(self, *, vacancy_id: str, source_text: str) -> VacancyExtraction:
        del source_text
        try:
            extraction = self._extraction_by_vacancy_id[vacancy_id]
        except KeyError as error:
            raise LookupError(f"No fake vacancy extraction for {vacancy_id}") from error
        return extraction.model_copy(deep=True)


class FakeCandidateEvidenceExtractor:
    model_name = "fake-candidate-extractor"
    model_version = "1"
    schema_version = "1"

    def __init__(
        self,
        extraction_by_cv_file_id: dict[str, CandidateEvidenceExtraction],
    ) -> None:
        self._extraction_by_cv_file_id = dict(extraction_by_cv_file_id)

    async def extract(
        self,
        *,
        user_id: str,
        cv_file_id: str,
        source_text: str,
        source_is_verified: bool = False,
    ) -> CandidateEvidenceExtraction:
        del user_id, source_text
        try:
            extraction = self._extraction_by_cv_file_id[cv_file_id]
        except KeyError as error:
            raise LookupError(f"No fake candidate extraction for {cv_file_id}") from error
        return extraction.model_copy(
            update={
                "evidence": [
                    evidence.model_copy(update={"is_verified": source_is_verified})
                    for evidence in extraction.evidence
                ]
            },
            deep=True,
        )
