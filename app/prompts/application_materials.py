from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import ProfileFact
from app.storage.tables import VacancyRow

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def build_materials_language_correction_prompt(prompt: str, required_language: str) -> str:
    return (
        f"{prompt}\n\nIMPORTANT: The previous response used the wrong language. "
        f"Return a newly written cover_letter_text entirely in {required_language}."
    )


def build_application_materials_prompt(
    *,
    vacancy: VacancyRow,
    facts: list[ProfileFact],
    open_fields: list[tuple[str, str]],
    response_language: str,
) -> str:
    vacancy_description = (vacancy.description_text or "").strip()[:6000]

    facts_payload = [
        {
            "category": str(fact.category),
            "name": str(fact.name),
            "value": str(fact.value),
        }
        for fact in facts
    ]

    screening_fields_payload = [
        {
            "field_id": field_id,
            "label": label,
        }
        for field_id, label in open_fields
    ]

    vacancy_payload = {
        "title": vacancy.title or "",
        "company": vacancy.company or "",
        "description": vacancy_description,
    }

    output_shape = {
        "cover_letter_text": "",
        "screening_answers": {},
    }

    common_rules = _load_prompt("materials_common.md")

    if response_language == "ru":
        language_rules = _load_prompt("materials_lang_ru.md")
    else:
        language_rules = _load_prompt("materials_lang_en.md")

    screening_rules = _load_prompt("materials_screening.md")

    return f"""
{common_rules}

{language_rules}

{screening_rules}

VACANCY DATA:
{json.dumps(vacancy_payload, ensure_ascii=False, indent=2)}

CANDIDATE FACTS:
{json.dumps(facts_payload, ensure_ascii=False, indent=2)}

OPEN SCREENING FIELDS:
{json.dumps(screening_fields_payload, ensure_ascii=False, indent=2)}

OUTPUT SHAPE:
{json.dumps(output_shape, ensure_ascii=False, indent=2)}
""".strip()
