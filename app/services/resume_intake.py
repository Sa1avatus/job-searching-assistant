"""Draft candidate profile facts from a resume, for human confirmation before they become usable.

Mirrors the safety shape already used in app/services/materials_generation.py: the model only
reads text the candidate themselves uploaded, and its output is never written to the database as
a "verified" fact automatically. ``ProfileFactRow.is_verified`` gates everything downstream —
vacancy matching (``assess_vacancy``), screening-answer autofill (``prepare_answers``), and
cover-letter drafting all only look at verified facts — so an LLM mis-reading a resume can, at
worst, produce a draft the human declines to confirm, never a fabricated claim sent to an
employer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass

_MAX_RESUME_CHARS = 12_000


class ExtractedProfileDraft(BaseModel):
    skills: list[str] = Field(default_factory=list, max_length=60)
    experience_summary: str = Field(default="", max_length=2_000)
    search_keywords: str = Field(default="", max_length=200)
    years_of_experience: float | None = None


def _build_prompt(resume_text: str) -> str:
    return (
        "Read this resume text and extract ONLY what is explicitly stated. Do not infer skills "
        "that are not mentioned, do not estimate seniority beyond what is stated, and do not "
        "invent employer names or dates.\n\n"
        f"Resume text:\n{resume_text[:_MAX_RESUME_CHARS]}\n\n"
        "Respond with a single JSON object matching exactly this shape:\n"
        "{\n"
        '  "skills": ["<short skill name>", ...],  // technologies, tools, '
        "languages actually named\n"
        '  "experience_summary": "<2-4 sentence factual summary of the candidate work history>",\n'
        '  "search_keywords": "<3-8 words a job search engine would use to find matching roles>",\n'
        '  "years_of_experience": <number or null if not determinable>\n'
        "}"
    )


class ResumeIntakeService:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def draft_profile(self, resume_text: str) -> ExtractedProfileDraft:
        request = ModelRequest(
            task_name="extract_resume_profile",
            task_class=ModelTaskClass.LOW_COST,
            prompt=_build_prompt(resume_text),
            max_cost_usd=0.05,
            timeout_seconds=45,
        )
        draft = await self._router.route(request, ExtractedProfileDraft)
        # Defense in depth against a model that ignores instructions: cap list size and dedupe,
        # never trust length/shape beyond what Pydantic already validated.
        seen: set[str] = set()
        deduped_skills: list[str] = []
        for skill in draft.skills:
            normalized = skill.strip()
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            deduped_skills.append(normalized)
        return draft.model_copy(update={"skills": deduped_skills})
