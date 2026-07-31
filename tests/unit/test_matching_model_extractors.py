import asyncio
from pathlib import Path

import pytest

from app.llm.router import ModelRouter
from app.matching.model_extractors import (
    RouterCandidateEvidenceExtractor,
    RouterVacancyRequirementExtractor,
    UngroundedExtractionError,
)
from app.prompts.registry import PromptRegistry


class _StaticProvider:
    name = "static"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def supports(self, task_class: object) -> bool:
        return True

    def estimate_cost_usd(self, request: object) -> float:
        return 0.0

    async def complete(self, request: object) -> dict[str, object]:
        return self.payload


def _prompt_registry() -> PromptRegistry:
    return PromptRegistry.load(Path(__file__).parents[2] / "prompts" / "registry.json")


def test_router_vacancy_extractor_accepts_source_grounded_requirements() -> None:
    async def run() -> None:
        source_text = "Requirements: Production experience with Python is required."
        provider = _StaticProvider(
            {
                "role": "Engineer",
                "seniority": None,
                "responsibilities": [],
                "constraints": [],
                "hard_blockers": [],
                "preferred_items": [],
                "confidence": 0.9,
                "requirements": [
                    {
                        "text": "Production experience with Python",
                        "normalized_text": "python production experience",
                        "requirement_type": "hard_skill",
                        "importance": "required",
                        "weight": 1,
                        "is_blocker": False,
                        "alternatives": [],
                        "source_fragment": "Production experience with Python is required.",
                        "source_section": "Requirements",
                        "confidence": 0.95,
                    }
                ],
            }
        )
        extractor = RouterVacancyRequirementExtractor(
            ModelRouter((provider,)),  # type: ignore[arg-type]
            _prompt_registry(),
        )

        extraction = await extractor.extract(vacancy_id="vacancy-1", source_text=source_text)

        assert extraction.requirements[0].normalized_text == "python production experience"

    asyncio.run(run())


def test_router_candidate_extractor_rejects_ungrounded_evidence() -> None:
    async def run() -> None:
        provider = _StaticProvider(
            {
                "confidence": 0.8,
                "evidence": [
                    {
                        "text": "Operated Kubernetes in production",
                        "normalized_text": "kubernetes production",
                        "evidence_type": "work_experience",
                        "skill_name": "Kubernetes",
                        "experience_level": "production",
                        "years": None,
                        "is_verified": True,
                        "source_fragment": "Operated Kubernetes in production",
                        "source_section": "Experience",
                        "confidence": 0.9,
                    }
                ],
            }
        )
        extractor = RouterCandidateEvidenceExtractor(
            ModelRouter((provider,)),  # type: ignore[arg-type]
            _prompt_registry(),
        )

        with pytest.raises(UngroundedExtractionError):
            await extractor.extract(
                user_id="user-1",
                cv_file_id="cv-1",
                source_text="Studied Kubernetes concepts.",
                source_is_verified=True,
            )

    asyncio.run(run())


def test_router_candidate_extractor_controls_verification_status() -> None:
    async def run() -> None:
        source_text = "Built a Kubernetes demo project."
        provider = _StaticProvider(
            {
                "confidence": 0.9,
                "evidence": [
                    {
                        "text": source_text,
                        "normalized_text": "kubernetes demo project",
                        "evidence_type": "project_experience",
                        "skill_name": "Kubernetes",
                        "experience_level": "project",
                        "years": None,
                        "is_verified": True,
                        "source_fragment": source_text,
                        "source_section": "Projects",
                        "confidence": 0.9,
                    }
                ],
            }
        )
        extractor = RouterCandidateEvidenceExtractor(
            ModelRouter((provider,)),  # type: ignore[arg-type]
            _prompt_registry(),
        )

        extraction = await extractor.extract(
            user_id="user-1",
            cv_file_id="cv-1",
            source_text=source_text,
        )

        assert not extraction.evidence[0].is_verified

    asyncio.run(run())
