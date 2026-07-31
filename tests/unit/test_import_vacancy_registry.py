from scripts.import_vacancy_registry import (
    canonicalize_vacancy_url,
    map_application_status,
    parse_registry_row,
)


def test_canonicalize_registry_job_urls() -> None:
    assert (
        canonicalize_vacancy_url(
            "https://vn.linkedin.com/jobs/view/ai-engineer-at-example-4431706001?tracking=x"
        )
        == "https://www.linkedin.com/jobs/view/4431706001"
    )
    assert canonicalize_vacancy_url("https://spb.hh.ru/vacancy/135233422?from=saved") == (
        "https://hh.ru/vacancy/135233422"
    )


def test_registry_statuses_preserve_outcome_meaning() -> None:
    assert map_application_status("Отказ") == "rejected"
    assert map_application_status("Собеседование") == "interview"
    assert map_application_status("Отклик не подтверждён") == "saved"
    assert map_application_status("Ожидает решения") == "submitted"


def test_parse_linkedin_row_builds_job_url_from_id() -> None:
    record = parse_registry_row(
        "LinkedIn",
        4,
        [1, "Example", "AI Engineer", "4431706001", "Singapore", 80, "Ожидает решения"],
    )

    assert record is not None
    assert record.source_url == "https://www.linkedin.com/jobs/view/4431706001"
    assert record.match_score == 80
