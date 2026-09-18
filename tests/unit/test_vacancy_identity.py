import pytest

from app.domain.vacancy_identity import (
    SOURCE_GREENHOUSE,
    SOURCE_HEADHUNTER,
    SOURCE_LINKEDIN,
    SOURCE_OTHER,
    SOURCE_REGISTRY,
    canonicalize_vacancy_url,
    vacancy_fingerprint,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://hh.ru/vacancy/123456",
        "https://spb.hh.ru/vacancy/123456?query=python&hhtmFrom=vacancy_search_list",
        "http://www.hh.ru/vacancy/123456/",
        "https://hh.ru/vacancy/123456#respond",
    ],
)
def test_headhunter_spellings_share_one_identity(url: str) -> None:
    identity = canonicalize_vacancy_url(url)

    assert identity.source_key == SOURCE_HEADHUNTER
    assert identity.source_id == "123456"
    assert identity.canonical_url == "https://hh.ru/vacancy/123456"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/3912345678/",
        "https://ru.linkedin.com/jobs/view/senior-python-engineer-3912345678?trk=public_jobs",
        "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=3912345678",
    ],
)
def test_linkedin_spellings_share_one_identity(url: str) -> None:
    identity = canonicalize_vacancy_url(url)

    assert (identity.source_key, identity.source_id) == (SOURCE_LINKEDIN, "3912345678")
    assert identity.canonical_url == "https://www.linkedin.com/jobs/view/3912345678"


def test_greenhouse_identity_includes_the_board() -> None:
    first = canonicalize_vacancy_url("https://boards.greenhouse.io/Acme/jobs/42?gh_src=x")
    second = canonicalize_vacancy_url("https://job-boards.greenhouse.io/acme/jobs/42")
    other_board = canonicalize_vacancy_url("https://boards.greenhouse.io/other/jobs/42")

    assert first == second
    assert first.source_key == SOURCE_GREENHOUSE and first.source_id == "acme/42"
    assert other_board.source_id != first.source_id


def test_generic_url_drops_tracking_fragment_and_sorts_query() -> None:
    a = canonicalize_vacancy_url("http://Jobs.Example.com/roles/7/?b=2&a=1&utm_source=x#top")
    b = canonicalize_vacancy_url("https://jobs.example.com/roles/7?a=1&b=2")

    assert a == b
    assert a.source_key == SOURCE_OTHER and a.source_id is None
    assert a.canonical_url == "https://jobs.example.com/roles/7?a=1&b=2"


def test_registry_adapter_keeps_its_source_key() -> None:
    identity = canonicalize_vacancy_url("https://example.com/evidence/1", "google-registry")

    assert identity.source_key == SOURCE_REGISTRY


def test_unparseable_input_never_raises() -> None:
    identity = canonicalize_vacancy_url("not a url at all")

    assert identity.source_key == SOURCE_OTHER


def test_fingerprint_ignores_case_punctuation_and_spacing() -> None:
    assert vacancy_fingerprint("ACME, Inc.", "Senior  Python-Dev", "Moscow") == vacancy_fingerprint(
        "acme inc", "senior python dev", "moscow"
    )
    assert vacancy_fingerprint("Acme", "Dev") != vacancy_fingerprint("Acme", "QA")
