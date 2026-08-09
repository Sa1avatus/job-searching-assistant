from app.domain.models import ProfileFact
from app.prompts.application_materials import (
    build_application_materials_prompt,
    build_materials_language_correction_prompt,
)
from app.storage.tables import VacancyRow


def test_application_materials_prompt_serializes_source_data_and_language() -> None:
    vacancy = VacancyRow(
        title="Backend Engineer",
        company="Example Corp",
        source_url="https://example.test/jobs/1",
        description_text="Build Python services.",
    )

    prompt = build_application_materials_prompt(
        vacancy=vacancy,
        facts=[ProfileFact(category="skill", name="Python", value="5 years")],
        open_fields=[("motivation", "Why this role?")],
        response_language="en",
    )

    assert "entirely in English" in prompt
    assert '"title": "Backend Engineer"' in prompt
    assert '"name": "Python"' in prompt
    assert '"field_id": "motivation"' in prompt


def test_materials_language_correction_prompt_is_centralized() -> None:
    prompt = build_materials_language_correction_prompt("base prompt", "Russian")

    assert prompt.startswith("base prompt")
    assert "entirely in Russian" in prompt
