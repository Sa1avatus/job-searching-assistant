"""Grounded cover-letter and screening-answer draft generation.

The LLM is only ever allowed to *draft* text. Drafts are never treated as verified
profile facts and never influence automatic submission on their own: they must pass
through the existing human-review materials endpoint (`update_application_materials`)
before they affect an application. This module enforces the deterministic half of
that boundary: it decides what the model is allowed to see and strips anything the
model returns that was not explicitly requested.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.domain.models import ProfileFact

MAX_COVER_LETTER_CHARACTERS = 4_000
MAX_ANSWER_CHARACTERS = 2_000


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    field_id: str
    label: str
    semantic_category: str
    is_required: bool


@dataclass(frozen=True, slots=True)
class MaterialsDraftContext:
    application_id: str
    vacancy_title: str
    vacancy_company: str
    vacancy_description: str
    required_skills: tuple[str, ...]
    verified_facts: tuple[ProfileFact, ...]
    open_questions: tuple[OpenQuestion, ...]

    def __post_init__(self) -> None:
        if any(not fact.is_verified for fact in self.verified_facts):
            raise ValueError("MaterialsDraftContext must only contain verified facts")


class GeneratedMaterials(BaseModel):
    """Structured, schema-validated output requested from the model provider."""

    cover_letter_text: str = Field(default="", max_length=MAX_COVER_LETTER_CHARACTERS)
    screening_answers: dict[str, str] = Field(default_factory=dict)
    grounding_notes: str = Field(default="", max_length=1_000)


def render_verified_facts_block(facts: tuple[ProfileFact, ...]) -> str:
    if not facts:
        return "(no verified facts on file)"
    return "\n".join(f"- [{fact.category}] {fact.name}: {fact.value}" for fact in facts)


def render_open_questions_block(questions: tuple[OpenQuestion, ...]) -> str:
    if not questions:
        return "(no open screening questions; draft only the cover letter)"
    lines = []
    for question in questions:
        required = "required" if question.is_required else "optional"
        lines.append(f"- field_id={question.field_id!r} ({required}): {question.label}")
    return "\n".join(lines)


def build_prompt_variables(context: MaterialsDraftContext) -> dict[str, str]:
    return {
        "vacancy_title": context.vacancy_title,
        "vacancy_company": context.vacancy_company,
        "vacancy_description": context.vacancy_description[:6_000],
        "required_skills": ", ".join(context.required_skills) or "(none listed)",
        "verified_facts": render_verified_facts_block(context.verified_facts),
        "open_fields": render_open_questions_block(context.open_questions),
    }


def sanitize_generated_materials(
    generated: GeneratedMaterials, context: MaterialsDraftContext
) -> GeneratedMaterials:
    """Deterministically enforce the execution-layer boundary on model output.

    Never trust the model to only answer the fields it was asked about, and never
    let it exceed the lengths the API/database accept. This runs regardless of what
    the model claims in `grounding_notes`.
    """
    allowed_field_ids = {question.field_id for question in context.open_questions}
    sanitized_answers = {
        field_id: answer.strip()[:MAX_ANSWER_CHARACTERS]
        for field_id, answer in generated.screening_answers.items()
        if field_id in allowed_field_ids and answer.strip()
    }
    return GeneratedMaterials(
        cover_letter_text=generated.cover_letter_text.strip()[:MAX_COVER_LETTER_CHARACTERS],
        screening_answers=sanitized_answers,
        grounding_notes=generated.grounding_notes.strip()[:1_000],
    )
