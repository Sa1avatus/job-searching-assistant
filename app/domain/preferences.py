"""User search preferences and the constraint check matching/application steps share.

Pure domain code: no storage, no adapters. Constraints are *soft*: a violation becomes a
visible warning on the application, it never silently drops a vacancy or changes a score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.vacancy_attributes import EMPLOYMENT_TYPE_ORDER

WORK_FORMATS = ("remote", "hybrid", "office")
CURRENCIES = ("RUB", "USD", "EUR", "KZT", "BYN", "GBP")

_CURRENCY_MARKERS = (
    ("RUB", re.compile(r"₽|\bруб|\brub\b|\brur\b", re.IGNORECASE)),
    ("USD", re.compile(r"\$|\busd\b|долл", re.IGNORECASE)),
    ("EUR", re.compile(r"€|\beur\b|евро", re.IGNORECASE)),
    ("KZT", re.compile(r"₸|\bkzt\b|тенге", re.IGNORECASE)),
    ("BYN", re.compile(r"\bbyn\b|бел\.?\s*руб", re.IGNORECASE)),
    ("GBP", re.compile(r"£|\bgbp\b", re.IGNORECASE)),
)
# "150 000", "150,000", "4000", "200k", "200 тыс"
_NUMBER = re.compile(r"(\d{1,3}(?:[  ,.]\d{3})+|\d+)\s*(k|тыс)?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class UserPreferences:
    min_salary: int | None = None
    salary_currency: str = "RUB"
    preferred_locations: tuple[str, ...] = ()
    work_formats: tuple[str, ...] = ()
    employment_types: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return (
            self.min_salary is None
            and not self.preferred_locations
            and not self.work_formats
            and not self.employment_types
        )


class InvalidPreferences(ValueError):
    pass


def validate_preferences(
    *,
    min_salary: int | None,
    salary_currency: str,
    preferred_locations: list[str] | tuple[str, ...],
    work_formats: list[str] | tuple[str, ...],
    employment_types: list[str] | tuple[str, ...],
) -> UserPreferences:
    """Normalise and validate raw input; raises InvalidPreferences on bad values."""
    if min_salary is not None and min_salary < 0:
        raise InvalidPreferences("min_salary must not be negative")
    currency = salary_currency.strip().upper()
    if currency not in CURRENCIES:
        raise InvalidPreferences(f"Unsupported currency: {salary_currency!r}")
    unknown_formats = sorted(set(work_formats) - set(WORK_FORMATS))
    if unknown_formats:
        raise InvalidPreferences(f"Unsupported work formats: {', '.join(unknown_formats)}")
    unknown_types = sorted(set(employment_types) - set(EMPLOYMENT_TYPE_ORDER))
    if unknown_types:
        raise InvalidPreferences(f"Unsupported employment types: {', '.join(unknown_types)}")
    locations: list[str] = []
    seen: set[str] = set()
    for raw in preferred_locations:
        name = " ".join(raw.split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            locations.append(name)
    return UserPreferences(
        min_salary=min_salary or None,
        salary_currency=currency,
        preferred_locations=tuple(locations),
        work_formats=tuple(f for f in WORK_FORMATS if f in work_formats),
        employment_types=tuple(t for t in EMPLOYMENT_TYPE_ORDER if t in employment_types),
    )


def parse_salary_range(text: str) -> tuple[int | None, int | None, str | None]:
    """Best-effort ``(low, high, currency)`` from free vacancy salary text.

    Returns (None, None, None) when nothing reliable can be read; a single number is both
    bounds. Amounts written with ``k``/``тыс`` are multiplied by 1000. Numbers below 1000
    without a multiplier are ignored (they are usually hours, years, or percentages).
    """
    if not text.strip():
        return None, None, None
    amounts: list[int] = []
    for match in _NUMBER.finditer(text):
        number = int(re.sub(r"[  ,.]", "", match.group(1)))
        if match.group(2):
            number *= 1000
        if number >= 1000:
            amounts.append(number)
    if not amounts:
        return None, None, None
    currency = next((code for code, marker in _CURRENCY_MARKERS if marker.search(text)), None)
    return min(amounts), max(amounts), currency


@dataclass(frozen=True, slots=True)
class VacancyFacts:
    location: str = ""
    work_format: str = "unspecified"
    employment_types: tuple[str, ...] = ()
    salary_text: str = ""


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


def evaluate_constraints(
    preferences: UserPreferences, vacancy: VacancyFacts
) -> list[ConstraintViolation]:
    """Return every preference the vacancy demonstrably contradicts.

    Unknown data never counts as a violation: an unspecified work format, an empty
    location, or unparseable/other-currency salary produces nothing.
    """
    violations: list[ConstraintViolation] = []

    if (
        preferences.work_formats
        and vacancy.work_format in WORK_FORMATS
        and vacancy.work_format not in preferences.work_formats
    ):
        violations.append(
            ConstraintViolation(
                "work_format",
                f"Work format {vacancy.work_format} is outside your preferences",
                {"vacancy": vacancy.work_format, "preferred": list(preferences.work_formats)},
            )
        )

    if (
        preferences.employment_types
        and vacancy.employment_types
        and not set(vacancy.employment_types) & set(preferences.employment_types)
    ):
        violations.append(
            ConstraintViolation(
                "employment_type",
                "Employment type is outside your preferences",
                {
                    "vacancy": list(vacancy.employment_types),
                    "preferred": list(preferences.employment_types),
                },
            )
        )

    if (
        preferences.preferred_locations
        and vacancy.location.strip()
        and vacancy.work_format != "remote"
    ):
        haystack = vacancy.location.casefold()
        if not any(place.casefold() in haystack for place in preferences.preferred_locations):
            violations.append(
                ConstraintViolation(
                    "location",
                    f"Location {vacancy.location} is outside your preferred locations",
                    {"vacancy": vacancy.location},
                )
            )

    if preferences.min_salary is not None:
        _, high, currency = parse_salary_range(vacancy.salary_text)
        if (
            high is not None
            and currency == preferences.salary_currency
            and high < preferences.min_salary
        ):
            violations.append(
                ConstraintViolation(
                    "salary",
                    f"Offered salary tops out at {high} {currency}, "
                    f"below your minimum {preferences.min_salary}",
                    {"offered_max": high, "minimum": preferences.min_salary},
                )
            )

    return violations
