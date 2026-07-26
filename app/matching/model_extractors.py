from __future__ import annotations

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.matching.extraction import (
    CandidateEvidenceExtraction,
    VacancyExtraction,
)
from app.prompts.registry import PromptRegistry

_MAX_SOURCE_CHARACTERS = 30_000


class UngroundedExtractionError(ValueError):
    pass


def _normalized_source(source_text: str) -> str:
    return " ".join(source_text.casefold().split())


def _is_grounded(source_text: str, source_fragment: str) -> bool:
    return _normalized_source(source_fragment) in _normalized_source(source_text)


class RouterVacancyRequirementExtractor:
    model_name = "model-router"
    model_version = "provider-selected"
    schema_version = "1"

    def __init__(self, router: ModelRouter, prompt_registry: PromptRegistry) -> None:
        self._router = router
        self._prompt_registry = prompt_registry

    async def extract(self, *, vacancy_id: str, source_text: str) -> VacancyExtraction:
        prompt = self._prompt_registry.render(
            "extract_vacancy_requirements",
            {
                "vacancy_id": vacancy_id,
                "source_text": source_text[:_MAX_SOURCE_CHARACTERS],
            },
        )
        extraction = await self._router.route(
            ModelRequest(
                task_name="extract_vacancy_requirements",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=60,
            ),
            VacancyExtraction,
        )
        ungrounded_fragments = [
            requirement.source_fragment
            for requirement in extraction.requirements
            if not _is_grounded(source_text, requirement.source_fragment)
        ]
        if ungrounded_fragments:
            raise UngroundedExtractionError(
                f"Vacancy extraction returned {len(ungrounded_fragments)} ungrounded fragments"
            )
        return extraction


class RouterCandidateEvidenceExtractor:
    model_name = "model-router"
    model_version = "provider-selected"
    schema_version = "1"

    def __init__(self, router: ModelRouter, prompt_registry: PromptRegistry) -> None:
        self._router = router
        self._prompt_registry = prompt_registry

    async def extract(
        self,
        *,
        user_id: str,
        cv_file_id: str,
        source_text: str,
        source_is_verified: bool = False,
    ) -> CandidateEvidenceExtraction:
        prompt = self._prompt_registry.render(
            "extract_candidate_evidence",
            {
                "user_id": user_id,
                "cv_file_id": cv_file_id,
                "source_text": source_text[:_MAX_SOURCE_CHARACTERS],
            },
        )
        extraction = await self._router.route(
            ModelRequest(
                task_name="extract_candidate_evidence",
                task_class=ModelTaskClass.LOW_COST,
                prompt=prompt,
                max_cost_usd=0.05,
                timeout_seconds=60,
            ),
            CandidateEvidenceExtraction,
        )
        ungrounded_fragments = [
            evidence.source_fragment
            for evidence in extraction.evidence
            if not _is_grounded(source_text, evidence.source_fragment)
        ]
        if ungrounded_fragments:
            raise UngroundedExtractionError(
                f"Candidate extraction returned {len(ungrounded_fragments)} ungrounded fragments"
            )
        return extraction.model_copy(
            update={
                "evidence": [
                    evidence.model_copy(update={"is_verified": source_is_verified})
                    for evidence in extraction.evidence
                ]
            }
        )
