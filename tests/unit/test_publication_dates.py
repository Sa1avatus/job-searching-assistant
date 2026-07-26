from datetime import UTC, datetime

from app.browser.publication_dates import parse_publication_datetime


def test_parse_iso_publication_datetime() -> None:
    assert parse_publication_datetime("2026-07-24T10:30:00Z") == datetime(
        2026, 7, 24, 10, 30, tzinfo=UTC
    )


def test_parse_relative_publication_datetime_in_both_languages() -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    assert parse_publication_datetime("2 days ago", now=now) == datetime(
        2026, 7, 24, 12, tzinfo=UTC
    )
    assert parse_publication_datetime("3 дня назад", now=now) == datetime(
        2026, 7, 23, 12, tzinfo=UTC
    )


def test_parse_explicit_russian_publication_date() -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    assert parse_publication_datetime("Вакансия опубликована 24 июля 2026", now=now) == datetime(
        2026, 7, 24, tzinfo=UTC
    )
