import asyncio

import httpx

from adapters.job_boards.headhunter_api import HeadHunterApi, HeadHunterVacancyReference
from adapters.job_boards.linkedin_reference import LinkedInJobReference


def test_headhunter_connector_extracts_public_vacancy_with_required_user_agent() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.hh.ru/vacancies/123456"
        assert request.headers["user-agent"] == "Assistant/1.0 (test@example.test)"
        assert request.headers["authorization"] == "Bearer test-oauth-token"
        return httpx.Response(
            200,
            json={
                "id": "123456",
                "name": "Python Developer",
                "description": "<p>Build <strong>reliable</strong> services</p>",
                "alternate_url": "https://hh.ru/vacancy/123456",
                "employer": {"name": "Example"},
                "area": {"name": "Москва"},
                "key_skills": [{"name": "Python"}, {"name": "PostgreSQL"}],
                "response_letter_required": True,
                "has_test": True,
            },
        )

    async def run_test() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            vacancy = await HeadHunterApi(
                client,
                user_agent="Assistant/1.0 (test@example.test)",
                access_token="test-oauth-token",
            ).extract_vacancy("https://spb.hh.ru/vacancy/123456?from=search")

        assert vacancy.title == "Python Developer"
        assert vacancy.required_skills == ("Python", "PostgreSQL")
        assert vacancy.source_url == "https://hh.ru/vacancy/123456"
        assert {field.field_id for field in vacancy.form_fields} == {"resume", "cover_letter"}
        assert vacancy.requires_sensitive_review is True

    asyncio.run(run_test())


def test_platform_references_reject_lookalikes_and_unsafe_urls() -> None:
    invalid_hh_urls = (
        "http://hh.ru/vacancy/123",
        "https://hh.ru.example.test/vacancy/123",
        "https://user:secret@hh.ru/vacancy/123",
        "https://hh.ru:8443/vacancy/123",
    )
    invalid_linkedin_urls = (
        "http://www.linkedin.com/jobs/view/123",
        "https://linkedin.com/jobs/view/123",
        "https://www.linkedin.com.example.test/jobs/view/123",
        "https://www.linkedin.com/in/member",
    )

    for url in invalid_hh_urls:
        try:
            HeadHunterVacancyReference.from_url(url)
        except ValueError:
            continue
        raise AssertionError(f"Unsafe hh.ru URL accepted: {url}")
    for url in invalid_linkedin_urls:
        try:
            LinkedInJobReference.from_url(url)
        except ValueError:
            continue
        raise AssertionError(f"Unsafe LinkedIn URL accepted: {url}")


def test_linkedin_connector_is_reference_only_and_canonical() -> None:
    reference = LinkedInJobReference.from_url(
        "https://www.linkedin.com/jobs/view/senior-engineer-987654321?trackingId=test"
    )

    assert reference.job_id == "987654321"
    assert reference.source_url == "https://www.linkedin.com/jobs/view/987654321"
    assert reference.extraction_mode == "manual"
    assert reference.submission_supported is False
