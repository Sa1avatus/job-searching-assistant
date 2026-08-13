from app.matching.vacancy_source import (
    VACANCY_MATCHING_SOURCE_VERSION,
    build_vacancy_matching_source,
)
from app.storage.tables import VacancyRow


def test_matching_source_includes_structured_skills_and_description() -> None:
    vacancy = VacancyRow(
        title="ML Engineer",
        company="Example",
        source_url="https://example.test/jobs/1",
        required_skills=["Python", "Postgres", "RAG"],
        preferred_skills=["PostgreSQL", "Docker"],
        description_text="Build production ML services.",
    )

    source = build_vacancy_matching_source(vacancy)

    assert f"Matching source version: {VACANCY_MATCHING_SOURCE_VERSION}" in source
    assert "Required skills:\n- Python\n- Postgres\n- RAG" in source
    assert "Preferred skills:\n- Docker" in source
    assert "PostgreSQL" not in source
    assert "Vacancy description:\nBuild production ML services." in source


def test_matching_source_changes_when_structured_skills_change() -> None:
    vacancy = VacancyRow(
        title="Engineer",
        company="Example",
        source_url="https://example.test/jobs/2",
        required_skills=["Python"],
        description_text="Build services.",
    )
    before = build_vacancy_matching_source(vacancy)

    vacancy.required_skills = ["Python", "Redis"]

    assert build_vacancy_matching_source(vacancy) != before
