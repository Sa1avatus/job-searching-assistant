from __future__ import annotations

import pytest

from app.domain.vacancy_attributes import detect_employment_types, find_salary_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Full-time role", ("full_time",)),
        ("Полная занятость", ("full_time",)),
        ("Part time role", ("part_time",)),
        ("Частичная занятость", ("part_time",)),
        ("Contract role", ("contract",)),
        ("Контрактная работа", ("contract",)),
        ("Project-based role", ("project",)),
        ("Проектная занятость", ("project",)),
        ("Temporary assignment", ("temporary",)),
        ("Временная работа", ("temporary",)),
        ("Internship", ("internship",)),
        ("Стажировка", ("internship",)),
    ],
)
def test_employment_type_is_detected_in_both_languages(
    text: str,
    expected: tuple[str, ...],
) -> None:
    assert detect_employment_types(text) == expected


def test_employment_types_are_deduplicated_in_canonical_order() -> None:
    result = detect_employment_types(
        "Internship and temporary project-based contract",
        "part-time or full-time; contract",
    )

    assert result == (
        "full_time",
        "part_time",
        "contract",
        "project",
        "temporary",
        "internship",
    )


def test_employment_type_substrings_do_not_match() -> None:
    assert detect_employment_types("Contractor tooling and projective geometry") == ()


@pytest.mark.parametrize(
    "source_line",
    [
        "Salary: $1000",
        "We offer compensation of EUR 2500 per month",
        "Зарплата: 100 000 руб.",
        "Pay: 80 000 THB",
        "£75,000 per year",
        "500 000 ₸",
        "Оплата 120 000 ₽",
    ],
)
def test_salary_line_is_preserved_from_supported_source(source_line: str) -> None:
    assert find_salary_text(source_line) == source_line


def test_first_salary_line_is_selected_and_whitespace_is_normalized() -> None:
    result = find_salary_text(
        "Founded in 2010\n  Salary:   $1000 per month  ",
        "Compensation: EUR 2000",
    )

    assert result == "Salary: $1000 per month"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Salary negotiable",
        "Company founded in 2010",
        "We employ 100 engineers",
    ],
)
def test_unsubstantiated_salary_is_not_returned(text: str) -> None:
    assert find_salary_text(text) == ""


def test_salary_line_is_truncated_within_limit_on_word_boundary() -> None:
    text = "Salary: $1000 per month with annual performance bonus"

    result = find_salary_text(text, max_characters=31)

    assert result == "Salary: $1000 per month with…"
    assert len(result) <= 31


def test_salary_line_with_one_character_limit_returns_ellipsis() -> None:
    assert find_salary_text("Salary: $1000", max_characters=1) == "…"


@pytest.mark.parametrize("max_characters", [0, -1])
def test_salary_line_rejects_non_positive_limit(max_characters: int) -> None:
    with pytest.raises(ValueError, match="max_characters must be positive"):
        find_salary_text("Salary: $1000", max_characters=max_characters)
