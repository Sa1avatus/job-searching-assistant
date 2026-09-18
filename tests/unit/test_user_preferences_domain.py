import pytest

from app.domain.preferences import (
    InvalidPreferences,
    UserPreferences,
    VacancyFacts,
    evaluate_constraints,
    parse_salary_range,
    validate_preferences,
)


def _prefs(**overrides):
    base = {
        "min_salary": None,
        "salary_currency": "RUB",
        "preferred_locations": [],
        "work_formats": [],
        "employment_types": [],
    }
    base.update(overrides)
    return validate_preferences(**base)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("от 200 000 ₽", (200000, 200000, "RUB")),
        ("150 000 - 250 000 руб.", (150000, 250000, "RUB")),
        ("$4000-6000", (4000, 6000, "USD")),
        ("200k EUR", (200000, 200000, "EUR")),
        ("по договоренности", (None, None, None)),
        ("", (None, None, None)),
    ],
)
def test_parse_salary_range(text: str, expected: tuple) -> None:
    assert parse_salary_range(text) == expected


def test_validation_normalises_and_orders_values() -> None:
    prefs = _prefs(
        min_salary=0,
        salary_currency=" usd ",
        preferred_locations=["  Москва ", "москва", "Berlin"],
        work_formats=["office", "remote"],
        employment_types=["part_time", "full_time"],
    )

    assert prefs.min_salary is None  # 0 means "no minimum"
    assert prefs.salary_currency == "USD"
    assert prefs.preferred_locations == ("Москва", "Berlin")
    assert prefs.work_formats == ("remote", "office")
    assert prefs.employment_types == ("full_time", "part_time")


@pytest.mark.parametrize(
    "bad",
    [
        {"min_salary": -1},
        {"salary_currency": "XXX"},
        {"work_formats": ["underwater"]},
        {"employment_types": ["forever"]},
    ],
)
def test_validation_rejects_bad_values(bad: dict) -> None:
    with pytest.raises(InvalidPreferences):
        _prefs(**bad)


def test_empty_preferences_never_violate() -> None:
    vacancy = VacancyFacts("Tokyo", "office", ("contract",), "$1000")

    assert UserPreferences().is_empty()
    assert evaluate_constraints(UserPreferences(), vacancy) == []


def test_each_constraint_reports_its_own_violation() -> None:
    prefs = _prefs(
        min_salary=300000,
        preferred_locations=["Москва"],
        work_formats=["remote"],
        employment_types=["full_time"],
    )
    vacancy = VacancyFacts("Санкт-Петербург", "office", ("contract",), "до 200 000 ₽")

    codes = {v.code for v in evaluate_constraints(prefs, vacancy)}

    assert codes == {"work_format", "employment_type", "location", "salary"}


def test_unknown_data_is_not_a_violation() -> None:
    prefs = _prefs(
        min_salary=300000,
        preferred_locations=["Москва"],
        work_formats=["remote"],
        employment_types=["full_time"],
    )

    assert evaluate_constraints(prefs, VacancyFacts()) == []
    # salary in another currency cannot be compared
    assert evaluate_constraints(prefs, VacancyFacts(salary_text="$1000")) == []


def test_remote_vacancy_ignores_location_preference() -> None:
    prefs = _prefs(preferred_locations=["Москва"])

    assert evaluate_constraints(prefs, VacancyFacts("Berlin", "remote")) == []
    assert evaluate_constraints(prefs, VacancyFacts("Москва, Россия", "office")) == []
