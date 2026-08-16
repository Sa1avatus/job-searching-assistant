import json

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
        required_skills=["Python", "PostgreSQL"],
        preferred_skills=["Docker"],
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
    assert '"required_skills": [' in prompt
    assert '"preferred_skills": [' in prompt
    assert '"key_skills": [' in prompt
    assert '"confirmed_candidate_key_skills": [' in prompt
    assert '"Python"' in prompt


def test_application_materials_prompt_only_confirms_candidate_owned_key_skills() -> None:
    vacancy = VacancyRow(
        title="ML Engineer",
        company="Example Corp",
        source_url="https://example.test/jobs/2",
        description_text="Build RAG services with PostgreSQL and Docker.",
        required_skills=["Python", "Postgres", "RAG"],
        preferred_skills=["Docker"],
    )

    prompt = build_application_materials_prompt(
        vacancy=vacancy,
        facts=[
            ProfileFact(category="skill", name="Python", value="production APIs"),
            ProfileFact(category="skills", name="PostgreSQL", value="query optimization"),
        ],
        open_fields=[],
        response_language="en",
    )

    vacancy_payload_text = prompt.rsplit("VACANCY DATA:\n", 1)[1].split("\n\nCANDIDATE FACTS:", 1)[
        0
    ]
    vacancy_payload = json.loads(vacancy_payload_text)

    assert vacancy_payload["key_skills"] == ["Python", "Postgres", "RAG", "Docker"]
    assert vacancy_payload["confirmed_candidate_key_skills"] == ["Python", "Postgres"]


def test_materials_language_correction_prompt_is_centralized() -> None:
    prompt = build_materials_language_correction_prompt("base prompt", "Russian")

    assert prompt.startswith("base prompt")
    assert "entirely in Russian" in prompt
