import dataclasses
import json
from datetime import UTC, datetime

from adapters.job_boards.headhunter_browser import ExtractedHeadHunterVacancy
from app.domain.forms import FieldConstraints, FormField, FormFieldType
from app.services.http_adapters import _ExtractedVacancy


def test_extracted_vacancy_rebuilds_types_lost_in_json() -> None:
    original = ExtractedHeadHunterVacancy(
        source_url="https://hh.ru/vacancy/1",
        title="Engineer",
        company="Acme",
        location="Remote",
        description_text="text",
        required_skills=("python", "sql"),
        form_fields=(
            FormField(
                field_id="letter",
                label="Cover letter",
                field_type=FormFieldType.TEXTAREA,
                is_required=True,
                options=("a",),
                constraints=FieldConstraints(max_length=100, accepted_file_types=("pdf",)),
            ),
        ),
        requires_sensitive_review=False,
        published_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    payload = json.loads(json.dumps(dataclasses.asdict(original), default=str))

    restored = _ExtractedVacancy(payload)

    assert restored.published_at == original.published_at
    assert restored.required_skills == ("python", "sql")
    (field,) = restored.form_fields
    assert isinstance(field, FormField)
    assert field.field_type is FormFieldType.TEXTAREA
    assert field.constraints.accepted_file_types == ("pdf",)
    assert field == original.form_fields[0]


def test_extracted_vacancy_tolerates_missing_optional_values() -> None:
    restored = _ExtractedVacancy({"title": "x"})

    assert restored.published_at is None
    assert restored.form_fields == ()
    assert restored.required_skills == ()
