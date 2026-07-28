from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from app.llm.router import ModelRequest
from app.services.resume_intake import (
    ExtractedProfileDraft,
    ResumeIntakeService,
    _build_prompt,
    _select_resume_text,
)


class FakeRouter:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.last_request: ModelRequest | None = None

    async def route(
        self,
        request: ModelRequest,
        output_schema: type[ExtractedProfileDraft],
    ) -> ExtractedProfileDraft:
        self.last_request = request
        return output_schema.model_validate(self.payload)


def _profile_payload(skills: list[str]) -> dict[str, Any]:
    return {
        "skills": skills,
        "experience_summary": "",
        "search_keywords": "",
        "years_of_experience": None,
    }


def test_resume_selection_preserves_bounded_beginning_and_end() -> None:
    resume_text = "BEGINNING" + ("x" * 60_000) + "SKILLS_AT_END"

    selected_text = _select_resume_text(resume_text)

    assert len(selected_text) <= 48_000
    assert selected_text.startswith("BEGINNING")
    assert selected_text.endswith("SKILLS_AT_END")
    assert "middle of resume omitted" in selected_text


def test_prompt_requests_exhaustive_explicit_skill_categories() -> None:
    prompt = _build_prompt("Python and PostgreSQL")

    for category in (
        "programming language",
        "framework",
        "database",
        "cloud service",
        "enterprise product",
        "protocol",
        "testing tool",
        "methodology",
        "professional certification",
        "spoken language",
    ):
        assert category in prompt
    assert "Do not merge distinct technologies" in prompt
    assert "do not infer any skill that is absent" in prompt


def test_prompt_contains_skills_listed_at_end_of_long_resume() -> None:
    prompt = _build_prompt(("a" * 60_000) + "RareSkillAtEnd")

    assert "RareSkillAtEnd" in prompt


def test_skill_deduplication_is_case_insensitive_and_stable() -> None:
    router = FakeRouter(
        _profile_payload([" Python ", "python", "", "Java", "JAVA", "PostgreSQL"])
    )

    draft = asyncio.run(ResumeIntakeService(router).draft_profile("resume"))

    assert draft.skills == ["Python", "Java", "PostgreSQL"]


def test_skill_inventory_preserves_160_unique_entries() -> None:
    skills = [f"Skill {index}" for index in range(160)]
    router = FakeRouter(_profile_payload(skills))

    draft = asyncio.run(ResumeIntakeService(router).draft_profile("resume"))

    assert draft.skills == skills


def test_skill_inventory_rejects_more_than_160_entries() -> None:
    router = FakeRouter(_profile_payload([f"Skill {index}" for index in range(161)]))

    with pytest.raises(ValidationError):
        asyncio.run(ResumeIntakeService(router).draft_profile("resume"))
