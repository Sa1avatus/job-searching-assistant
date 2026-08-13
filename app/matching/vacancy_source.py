from __future__ import annotations

from app.matching.normalization import SkillNormalizer
from app.storage.tables import VacancyRow

VACANCY_MATCHING_SOURCE_VERSION = "2"


def build_vacancy_matching_source(vacancy: VacancyRow) -> str:
    """Build the versioned, grounded source used by matching extraction."""
    normalizer = SkillNormalizer()
    required = _deduplicate_skills(vacancy.required_skills or (), normalizer)
    required_keys = {normalizer.normalize(skill).canonical.casefold() for skill in required}
    preferred = [
        skill
        for skill in _deduplicate_skills(vacancy.preferred_skills or (), normalizer)
        if normalizer.normalize(skill).canonical.casefold() not in required_keys
    ]

    sections = [
        f"Matching source version: {VACANCY_MATCHING_SOURCE_VERSION}",
        f"Vacancy title: {vacancy.title or ''}",
        f"Company: {vacancy.company or ''}",
    ]
    if required:
        sections.append("Required skills:\n" + "\n".join(f"- {skill}" for skill in required))
    if preferred:
        sections.append("Preferred skills:\n" + "\n".join(f"- {skill}" for skill in preferred))
    if vacancy.description_text:
        sections.append("Vacancy description:\n" + vacancy.description_text.strip())
    return "\n\n".join(sections).strip()


def _deduplicate_skills(
    skills: tuple[str, ...] | list[str],
    normalizer: SkillNormalizer,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_skill in skills:
        skill = " ".join(str(raw_skill).split())
        if not skill:
            continue
        key = normalizer.normalize(skill).canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(skill)
    return result
