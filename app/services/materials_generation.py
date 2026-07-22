"""Draft application materials (cover letter, free-text screening answers) with an LLM.

Hard safety rules, enforced in code (not just by prompting):
- The prompt includes only the candidate's *verified* profile facts (``ProfileFactRow.is_verified``)
  and the vacancy's own text. The model is instructed not to invent anything beyond that, but the
  instruction alone is not trusted.
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

from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.policy import SENSITIVE_CATEGORIES
from app.llm.router import ModelRequest, ModelRouter, ModelTaskClass
from app.services.recruitment import EntityNotFoundError
from app.storage.tables import ApplicationRow, ProfileFactRow, VacancyRow


class NoVerifiedFactsError(RuntimeError):
    """The candidate has no verified profile facts to draft from."""


class MaterialsDraft(BaseModel):
    cover_letter_text: str = Field(max_length=20_000)
    screening_answers: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GeneratedMaterials:
    cover_letter_text: str
    filled_field_ids: tuple[str, ...]
    skipped_sensitive_field_ids: tuple[str, ...]


def _build_prompt(
    *,
    vacancy: VacancyRow,
    facts: list[ProfileFactRow],
    open_fields: list[tuple[str, str]],
) -> str:
    fact_lines = "\n".join(f"- [{fact.category}] {fact.name}: {fact.value}" for fact in facts)
    field_lines = "\n".join(f"- field_id={field_id!r}: {label}" for field_id, label in open_fields)
    return (
        "You are drafting job-application materials for a real candidate. Use ONLY the facts "
        "listed below. Do not invent employers, dates, numbers, skills, or achievements that are "
        "not present in this list. If a screening question cannot be answered from these facts, "
        "omit it from screening_answers entirely rather than guessing.\n\n"
        f"Vacancy title: {vacancy.title}\n"
        f"Company: {vacancy.company}\n"
        f"Vacancy description:\n{(vacancy.description_text or '')[:4000]}\n\n"
        f"Candidate's verified facts:\n{fact_lines or '(none provided)'}\n\n"
        f"Open screening questions to optionally answer (besides the cover letter):\n"
        f"{field_lines or '(none)'}\n\n"
        "Respond with a single JSON object matching exactly this shape:\n"
        '{"cover_letter_text": "<3-5 short paragraphs, no placeholders>", '
        '"screening_answers": {"<field_id>": "<answer>", ...}}'
    )


class MaterialsGenerationService:
    def __init__(self, session: Session, router: ModelRouter) -> None:
        self._session = session
        self._router = router

    async def draft_materials(self, application_id: str) -> GeneratedMaterials:
        application = self._session.get(ApplicationRow, application_id)
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status != "awaiting_review":
            raise EntityNotFoundError("Application is no longer awaiting review")
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            raise EntityNotFoundError("Vacancy not found")

        facts = list(
            self._session.scalars(
                select(ProfileFactRow).where(
                    ProfileFactRow.user_id == application.user_id,
                    ProfileFactRow.is_verified.is_(True),
                )
            )
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

        prompt = _build_prompt(vacancy=vacancy, facts=facts, open_fields=open_fields)
        request = ModelRequest(
            task_name="draft_application_materials",
            task_class=ModelTaskClass.LOW_COST,
            prompt=prompt,
            max_cost_usd=0.05,
            timeout_seconds=45,
        )
        draft = await self._router.route(request, MaterialsDraft)

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

        if not (application.cover_letter_text or "").strip():
            application.cover_letter_text = draft.cover_letter_text.strip()
        self._session.commit()
        return GeneratedMaterials(
            cover_letter_text=application.cover_letter_text,
            filled_field_ids=tuple(filled),
            skipped_sensitive_field_ids=tuple(skipped_sensitive),
        )
