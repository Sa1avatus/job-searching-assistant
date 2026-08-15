from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailEntities:
    company: str | None
    vacancy_title: str | None
    confidence: float


_COMPANY_PATTERNS = (
    re.compile(
        r"(?:from|от)\s+([A-ZА-Я][A-Za-zА-Яа-я\s&.]{2,40})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:team|recruiting|careers|hr)\b",
        re.IGNORECASE,
    ),
)

_VACANCY_PATTERNS = (
    re.compile(
        r"^(.{5,80}?)\s+(?:position|role|vacancy)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:re|for|по вакансии|на позицию)\s*[:\-]?\s*(.{5,80}?)(?:\s*[-–—|]|\s*$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:position|role|vacancy|ваканси[яюе]|позици[яюе])\s*[:\-]?\s*(.{5,80}?)(?:\s*[-–—|]|\s*$)",
        re.IGNORECASE,
    ),
)


def extract_email_entities(subject: str, body: str = "") -> EmailEntities:
    text = subject.strip()
    company = _extract_first(_COMPANY_PATTERNS, text)
    vacancy_title = _extract_first(_VACANCY_PATTERNS, text)
    if vacancy_title is None and body:
        vacancy_title = _extract_first(_VACANCY_PATTERNS, body[:500])
    confidence = 0.0
    if company is not None:
        confidence += 0.5
    if vacancy_title is not None:
        confidence += 0.5
    return EmailEntities(
        company=company,
        vacancy_title=vacancy_title,
        confidence=confidence,
    )


def _extract_first(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            extracted = match.group(1).strip()
            if len(extracted) >= 3:
                return extracted
    return None
