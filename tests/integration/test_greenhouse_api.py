import asyncio
import json
from pathlib import Path

import httpx

from adapters.job_boards.greenhouse_api import (
    GreenhouseJobBoardApi,
    GreenhouseJobPayload,
    GreenhouseJobReference,
)
from app.domain.forms import FormFieldType


def test_greenhouse_api_extracts_vacancy_and_typed_questions() -> None:
    payload_path = Path(__file__).parents[1] / "fixtures" / "greenhouse_job.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["questions"] == "true"
        return httpx.Response(200, json=payload)

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = GreenhouseJobBoardApi(client)
            job = await adapter.extract_job("https://boards.greenhouse.io/example/jobs/44444")

        fields_by_id = {field.field_id: field for field in job.form_fields}
        assert job.title == "Product Engineer"
        assert "Build reliable Python services." in job.description_text
        assert fields_by_id["resume"].field_type is FormFieldType.FILE
        assert fields_by_id["question_2222"].options == ("No", "Yes")
        assert fields_by_id["eeoc_gender"].semantic_category == "protected_demographic"
        assert job.requires_sensitive_review is True

    asyncio.run(run_test())


def test_greenhouse_reference_rejects_path_and_host_injection() -> None:
    invalid_urls = (
        "https://boards.greenhouse.io.example.test/example/jobs/44444",
        "http://boards.greenhouse.io/example/jobs/44444",
        "https://user:secret@boards.greenhouse.io/example/jobs/44444",
        "https://boards.greenhouse.io:8443/example/jobs/44444",
        "https://boards.greenhouse.io/example/jobs/not-a-number",
        "https://boards.greenhouse.io/example/other/44444",
    )

    for url in invalid_urls:
        try:
            GreenhouseJobReference.from_url(url)
        except ValueError:
            continue
        raise AssertionError(f"Unsafe Greenhouse URL accepted: {url}")


def test_greenhouse_reference_canonicalizes_tracking_parameters() -> None:
    reference = GreenhouseJobReference.from_url(
        "https://job-boards.greenhouse.io/example/jobs/44444?gh_src=tracking#section"
    )

    assert reference.source_url == "https://job-boards.greenhouse.io/example/jobs/44444"


def test_greenhouse_payload_normalizes_nullable_api_collections() -> None:
    payload = GreenhouseJobPayload.model_validate(
        {
            "id": 1,
            "title": "Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/example/jobs/1",
            "questions": None,
            "location_questions": None,
            "compliance": None,
            "data_compliance": None,
        }
    )

    assert payload.questions == []
    assert payload.compliance == []
