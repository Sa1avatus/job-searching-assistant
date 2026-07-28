from __future__ import annotations

import re
from typing import Literal

EmploymentType = Literal[
    "full_time",
    "part_time",
    "contract",
    "project",
    "temporary",
    "internship",
]

EMPLOYMENT_TYPE_ORDER: tuple[EmploymentType, ...] = (
    "full_time",
    "part_time",
    "contract",
    "project",
    "temporary",
    "internship",
)

_EMPLOYMENT_PATTERNS: dict[EmploymentType, tuple[re.Pattern[str], ...]] = {
    "full_time": (
        re.compile(r"(?<!\w)full[\s-]*time(?!\w)", re.IGNORECASE),
        re.compile(r"(?<!\w)полная\s+занятость(?!\w)", re.IGNORECASE),
    ),
    "part_time": (
        re.compile(r"(?<!\w)part[\s-]*time(?!\w)", re.IGNORECASE),
        re.compile(r"(?<!\w)(?:частичная|неполная)\s+занятость(?!\w)", re.IGNORECASE),
    ),
    "contract": (
        re.compile(r"(?<!\w)contract(?!\w)", re.IGNORECASE),
        re.compile(r"(?<!\w)контракт(?:ная|ный|ное|ные)?(?!\w)", re.IGNORECASE),
        re.compile(r"(?<!\w)по\s+договору(?!\w)", re.IGNORECASE),
    ),
    "project": (
        re.compile(r"(?<!\w)project(?:[\s-]+based)?(?!\w)", re.IGNORECASE),
        re.compile(
            r"(?<!\w)проектн(?:ая|ый|ое|ые)\s+(?:работа|занятость)(?!\w)",
            re.IGNORECASE,
        ),
    ),
    "temporary": (
        re.compile(r"(?<!\w)temporary(?!\w)", re.IGNORECASE),
        re.compile(
            r"(?<!\w)временн(?:ая|ый|ое|ые)\s+(?:работа|занятость)(?!\w)",
            re.IGNORECASE,
        ),
    ),
    "internship": (
        re.compile(r"(?<!\w)internship(?!\w)", re.IGNORECASE),
        re.compile(r"(?<!\w)intern\s+(?:role|position)(?!\w)", re.IGNORECASE),
        re.compile(r"(?<!\w)стажировк(?:а|и|у|ой)(?!\w)", re.IGNORECASE),
    ),
}

_DIGIT_PATTERN = re.compile(r"\d")
_SALARY_INDICATOR_PATTERN = re.compile(
    r"(?<!\w)(?:salary|pay|compensation|зарплат[а-яё]*|оплат[а-яё]*)(?!\w)"
    r"|(?<!\w)(?:USD|EUR|GBP|THB|KZT|руб\.?)(?!\w)"
    r"|[₽$€£฿₸]",
    re.IGNORECASE,
)


def detect_employment_types(*texts: str) -> tuple[EmploymentType, ...]:
    """Return source-grounded employment tags in canonical order."""
    matched_types: set[EmploymentType] = set()
    for text in texts:
        for employment_type, patterns in _EMPLOYMENT_PATTERNS.items():
            if any(pattern.search(text) for pattern in patterns):
                matched_types.add(employment_type)
    return tuple(
        employment_type
        for employment_type in EMPLOYMENT_TYPE_ORDER
        if employment_type in matched_types
    )


def find_salary_text(*texts: str, max_characters: int = 240) -> str:
    """Return the first normalized source line that visibly describes compensation."""
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")

    for text in texts:
        for source_line in text.splitlines():
            normalized_line = " ".join(source_line.split())
            if not normalized_line:
                continue
            if not _DIGIT_PATTERN.search(normalized_line):
                continue
            if not _SALARY_INDICATOR_PATTERN.search(normalized_line):
                continue
            if len(normalized_line) <= max_characters:
                return normalized_line
            if max_characters == 1:
                return "…"
            available_characters = max_characters - 1
            truncated_line = normalized_line[:available_characters]
            if (
                len(normalized_line) > available_characters
                and not normalized_line[available_characters].isspace()
            ):
                last_space_index = truncated_line.rfind(" ")
                if last_space_index > 0:
                    truncated_line = truncated_line[:last_space_index]
            return truncated_line.rstrip() + "…"
    return ""
