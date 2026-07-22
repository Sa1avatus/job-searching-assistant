from __future__ import annotations

from collections.abc import Iterable

from app.domain.models import (
    ApplicationQuestion,
    MatchAssessment,
    PreparedAnswer,
    ProfileFact,
    SubmissionMode,
    Vacancy,
)

SENSITIVE_CATEGORIES = frozenset(
    {
        "background_check",
        "criminal_history",
        "data_processing_consent",
        "disability",
        "export_control",
        "legal_declaration",
        "medical_information",
        "military_status",
        "protected_demographic",
        "work_authorization",
    }
)


def assess_vacancy(vacancy: Vacancy, profile_facts: Iterable[ProfileFact]) -> MatchAssessment:
    verified_skills = {
        fact.name.casefold()
        for fact in profile_facts
        if fact.is_verified and fact.category == "skill"
    }
    required = {skill.casefold() for skill in vacancy.required_skills}
    preferred = {skill.casefold() for skill in vacancy.preferred_skills}
    matched_required = required & verified_skills
    missing_required = required - verified_skills
    matched_preferred = preferred & verified_skills

    required_score = 70 * len(matched_required) / max(len(required), 1)
    preferred_score = 30 * len(matched_preferred) / max(len(preferred), 1)
    score = round(required_score + preferred_score)
    if missing_required:
        recommendation = "review" if score >= 50 else "skip"
    else:
        recommendation = "apply" if score >= 70 else "review"
    return MatchAssessment(
        score=score,
        matched_required_skills=tuple(sorted(matched_required)),
        missing_required_skills=tuple(sorted(missing_required)),
        matched_preferred_skills=tuple(sorted(matched_preferred)),
        recommendation=recommendation,
    )


def prepare_answers(
    questions: Iterable[ApplicationQuestion], profile_facts: Iterable[ProfileFact]
) -> tuple[PreparedAnswer, ...]:
    verified_fact_by_name = {
        fact.name.casefold(): fact for fact in profile_facts if fact.is_verified
    }
    answers: list[PreparedAnswer] = []
    for question in questions:
        fact = verified_fact_by_name.get(question.semantic_category.casefold())
        is_sensitive = question.semantic_category in SENSITIVE_CATEGORIES
        if fact is None:
            answers.append(
                PreparedAnswer(
                    field_id=question.field_id,
                    answer=None,
                    source_fact_name=None,
                    requires_review=question.is_required,
                    warning="Missing verified profile fact" if question.is_required else None,
                )
            )
            continue
        answers.append(
            PreparedAnswer(
                field_id=question.field_id,
                answer=fact.value,
                source_fact_name=fact.name,
                requires_review=is_sensitive,
                warning="Sensitive declaration requires human review" if is_sensitive else None,
            )
        )
    return tuple(answers)


def can_submit(
    mode: SubmissionMode,
    answers: Iterable[PreparedAnswer],
    *,
    source_is_authorized: bool,
) -> bool:
    prepared_answers = tuple(answers)
    return (
        mode is SubmissionMode.AUTOMATIC
        and source_is_authorized
        and all(
            answer.answer is not None and not answer.requires_review for answer in prepared_answers
        )
    )
