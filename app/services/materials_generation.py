"""Draft application materials (cover letter, free-text screening answers) with an LLM.

Hard safety rules, enforced in code (not just by prompting):
- The prompt includes only the candidate's verified global facts plus facts extracted from the CV
  selected on the application. The model is instructed not to invent anything beyond that, but
  the instruction alone is not trusted.
- Sensitive-category fields (work authorization, disability, background checks, ...) are never
  sent to the model and never overwritten by its output, even if the model tries to answer them
  anyway. See ``app/domain/policy.SENSITIVE_CATEGORIES``.
- Generated text always lands as a draft on an ``awaiting_review`` application with
  ``answer_source="llm_generated"``; nothing here schedules a submission. A human still reviews
  and edits (or clears) it via ``PATCH /v1/applications/{id}/materials`` before any apply step,
  and ``RecruitmentService.schedule_real_submission_apply`` still gates sensitive answers on an
  explicit ``human_review`` source regardless of what this service ever writes.
- Existing non-empty answers (already supplied by a human or a previous fact match) are never
  overwritten, so this is safe to re-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.domain.policy import SENSITIVE_CATEGORIES
from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.prompts.application_materials import (
    build_application_materials_prompt,
    build_materials_language_correction_prompt,
)
from app.services.recruitment import EntityNotFoundError, RecruitmentService
from app.storage.tables import ApplicationRow, VacancyRow


class NoVerifiedFactsError(RuntimeError):
    """The candidate has no verified profile facts to draft from."""


class MaterialsLanguageMismatchError(RuntimeError):
    """The model ignored the required vacancy language twice."""


class MaterialsDraft(BaseModel):
    cover_letter_text: str = Field(max_length=20_000)
    screening_answers: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GeneratedMaterials:
    cover_letter_text: str
    filled_field_ids: tuple[str, ...]
    skipped_sensitive_field_ids: tuple[str, ...]


def detect_vacancy_language(vacancy: VacancyRow) -> Literal["ru", "en"]:
    vacancy_text = vacancy.description_text or ""
    cyrillic_count = len(re.findall(r"[А-Яа-яЁё]", vacancy_text))
    latin_count = len(re.findall(r"[A-Za-z]", vacancy_text))
    # Job titles commonly remain in English even when the actual vacancy is Russian.
    # Prefer a substantial, clearly dominant description; use the title only when the
    # description is short, mixed, or mostly page-interface noise.
    if cyrillic_count >= 80 and cyrillic_count >= latin_count:
        return "ru"
    if latin_count >= 80 and latin_count >= cyrillic_count * 3:
        return "en"

    title_cyrillic = len(re.findall(r"[А-Яа-яЁё]", vacancy.title))
    title_latin = len(re.findall(r"[A-Za-z]", vacancy.title))
    if title_latin >= 10 and title_cyrillic < 3:
        return "en"
    if title_cyrillic >= 3 and title_cyrillic >= title_latin / 2:
        return "ru"
    if cyrillic_count >= 10 or (cyrillic_count >= 3 and cyrillic_count >= latin_count / 3):
        return "ru"
    return "en"


def _is_russian_text(text: str) -> bool:
    return len(re.findall(r"[А-Яа-яЁё]", text)) >= 10


def _matches_language(text: str, language: str) -> bool:
    cyrillic_count = len(re.findall(r"[А-Яа-яЁё]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    if language == "ru":
        return _is_russian_text(text) or (
            cyrillic_count >= 3 and cyrillic_count >= latin_count / 3
        )
    return latin_count >= 10 and (
        cyrillic_count < 3 or latin_count >= cyrillic_count * 3
    )


def cover_letter_matches_vacancy_language(vacancy: VacancyRow, text: str) -> bool:
    return not text.strip() or _matches_language(text, detect_vacancy_language(vacancy))


class MaterialsGenerationService:
    def __init__(self, session: Session, router: ModelRouter) -> None:
        self._session = session
        self._router = router

    def needs_material_refresh(self, application_id: str) -> bool:
        """Return whether an awaiting-review application has missing or wrong-language material."""
        application = self._session.get(ApplicationRow, application_id)
        if application is None or application.status != "awaiting_review":
            return False
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            return False
        cover_letter = application.cover_letter_text or ""
        response_language = detect_vacancy_language(vacancy)
        if not cover_letter.strip() or not _matches_language(cover_letter, response_language):
            return True
        return any(
            not answer.answer
            and answer.semantic_category not in SENSITIVE_CATEGORIES
            and answer.field_id != "resume"
            for answer in application.answers
        )

    async def draft_materials(
        self,
        application_id: str,
        *,
        replace_mismatched_cover_letter: bool = False,
        force_replace_cover_letter: bool = False,
        timeout_seconds: float = 45,
    ) -> GeneratedMaterials:
        application = self._session.get(ApplicationRow, application_id)
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status != "awaiting_review":
            raise EntityNotFoundError("Application is no longer awaiting review")
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            raise EntityNotFoundError("Vacancy not found")

        facts = RecruitmentService(self._session).verified_profile_facts(
            application.user_id, application.selected_cv_file_id
        )
        if not facts:
            raise NoVerifiedFactsError(
                "No verified profile facts found; import a profile before drafting materials"
            )

        open_fields = [
            (answer.field_id, answer.label)
            for answer in application.answers
            if not answer.answer
            and answer.semantic_category not in SENSITIVE_CATEGORIES
            and answer.field_id != "resume"
        ]

        response_language = detect_vacancy_language(vacancy)
        prompt = build_application_materials_prompt(
            vacancy=vacancy,
            facts=facts,
            open_fields=open_fields,
            response_language=response_language,
        )
        request = ModelRequest(
            task_name="draft_application_materials",
            task_class=ModelTaskClass.LOW_COST,
            prompt=prompt,
            max_cost_usd=0.05,
            timeout_seconds=timeout_seconds,
        )
        draft = await self._router.route(request, MaterialsDraft)
        self._session.refresh(application, attribute_names=["status", "cover_letter_text"])
        if application.status != "awaiting_review":
            return GeneratedMaterials(
                cover_letter_text=application.cover_letter_text,
                filled_field_ids=(),
                skipped_sensitive_field_ids=(),
            )
        if not _matches_language(draft.cover_letter_text, response_language):
            required_language = "Russian" if response_language == "ru" else "English"
            correction_request = ModelRequest(
                task_name="correct_application_materials_language",
                task_class=ModelTaskClass.LOW_COST,
                prompt=build_materials_language_correction_prompt(
                    prompt,
                    required_language,
                ),
                max_cost_usd=0.05,
                timeout_seconds=timeout_seconds,
            )
            draft = await self._router.route(correction_request, MaterialsDraft)
            self._session.refresh(application, attribute_names=["status", "cover_letter_text"])
            if application.status != "awaiting_review":
                return GeneratedMaterials(
                    cover_letter_text=application.cover_letter_text,
                    filled_field_ids=(),
                    skipped_sensitive_field_ids=(),
                )
            if not _matches_language(draft.cover_letter_text, response_language):
                raise MaterialsLanguageMismatchError(
                    f"The model did not produce a {required_language} cover letter for a "
                    f"{required_language} vacancy"
                )

        filled: list[str] = []
        skipped_sensitive: list[str] = []
        answers_by_id = {answer.field_id: answer for answer in application.answers}
        for field_id, generated_answer in draft.screening_answers.items():
            answer = answers_by_id.get(field_id)
            if answer is None:
                continue
            if answer.semantic_category in SENSITIVE_CATEGORIES:
                # Defense in depth: sensitive fields are never sent to the model, but if it
                # hallucinates a matching field_id anyway, still refuse to write it.
                skipped_sensitive.append(field_id)
                continue
            if answer.answer:
                continue
            normalized = generated_answer.strip()
            if not normalized:
                continue
            answer.answer = normalized
            answer.answer_source = "llm_generated"
            answer.source_fact_name = None
            answer.warning = "Drafted by AI from your verified profile facts — review before use"
            filled.append(field_id)

        existing_cover_letter = application.cover_letter_text or ""
        should_replace_cover_letter = (
            force_replace_cover_letter
            or not existing_cover_letter.strip()
            or (
                replace_mismatched_cover_letter
                and not _matches_language(existing_cover_letter, response_language)
            )
        )
        if should_replace_cover_letter:
            application.cover_letter_text = draft.cover_letter_text.strip()
        self._session.commit()
        return GeneratedMaterials(
            cover_letter_text=application.cover_letter_text,
            filled_field_ids=tuple(filled),
            skipped_sensitive_field_ids=tuple(skipped_sensitive),
        )
