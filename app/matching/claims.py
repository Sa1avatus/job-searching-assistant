"""Atomic claim decomposition for vacancy requirements.

Each composite requirement is decomposed into atomic claims that can be
independently evaluated against candidate evidence. Logical relationships
(AND, OR, optional) are preserved.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ClaimType(StrEnum):
    """Supported atomic claim types."""

    SKILL = "skill"
    EXPERIENCE_DURATION = "experience_duration"
    PRACTICAL_EXPERIENCE = "practical_experience"
    PRODUCTION_EXPERIENCE = "production_experience"
    TECHNOLOGY = "technology"
    DOMAIN = "domain"
    LANGUAGE_LEVEL = "language_level"
    EDUCATION = "education"
    LOCATION = "location"
    WORK_AUTHORIZATION = "work_authorization"
    CERTIFICATION = "certification"
    AVAILABILITY = "availability"
    OTHER = "other"


class LogicalGroup(StrEnum):
    AND = "and"
    OR = "or"


class Criticality(StrEnum):
    """Criticality of an atomic claim within its requirement."""

    REQUIRED = "required"
    STRONGLY_PREFERRED = "strongly_preferred"
    PREFERRED = "preferred"
    BONUS = "bonus"
    HARD_BLOCKER = "hard_blocker"


class RequirementCriticality(StrEnum):
    """Aggregate criticality level for a vacancy requirement."""

    HARD_BLOCKER = "hard_blocker"
    REQUIRED = "required"
    STRONGLY_PREFERRED = "strongly_preferred"
    PREFERRED = "preferred"
    BONUS = "bonus"


class StrictClaimModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AtomicClaim(StrictClaimModel):
    """A single testable assertion derived from a requirement."""

    id: str = Field(min_length=1, max_length=200)
    requirement_id: str = Field(min_length=1, max_length=200)
    claim_type: ClaimType
    subject: str = Field(min_length=1, max_length=500)
    normalized_subject: str = Field(min_length=1, max_length=500)
    operator: str | None = Field(default=None, max_length=20)
    required_value: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=50)
    criticality: Criticality
    logical_group: LogicalGroup = LogicalGroup.AND
    source_text: str = Field(min_length=1, max_length=4_000)
    metadata: dict[str, object] = Field(default_factory=dict)


class RequirementDecomposition(StrictClaimModel):
    """Result of decomposing one vacancy requirement into atomic claims."""

    requirement_id: str
    requirement_text: str
    requirement_criticality: RequirementCriticality
    claims: list[AtomicClaim] = Field(default_factory=list, max_length=50)
    is_composite: bool = False
    decomposition_confidence: float = Field(ge=0, le=1, default=1.0)


class VacancyDecomposition(StrictClaimModel):
    """Full decomposition of all vacancy requirements."""

    vacancy_id: str
    requirements: list[RequirementDecomposition] = Field(max_length=200)
    total_claims: int = Field(ge=0)
    decomposition_model: str = ""
    decomposition_model_version: str = ""
    decomposition_schema_version: str = "1"


class RequirementDecomposer(Protocol):
    """Protocol for decomposing requirements into atomic claims."""

    model_name: str
    model_version: str
    schema_version: str

    async def decompose(
        self,
        *,
        requirement_id: str,
        requirement_text: str,
        requirement_type: str,
        importance: str,
        is_blocker: bool,
    ) -> RequirementDecomposition: ...
