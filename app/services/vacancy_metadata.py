"""Deterministic vacancy presentation metadata helpers."""

from __future__ import annotations

import re
from typing import Literal

_HYBRID_PATTERNS = [
    re.compile(r"\bhybrid\b", re.IGNORECASE),
    re.compile(r"\bhybrid\s+work\b", re.IGNORECASE),
    re.compile(r"\bhybrid\s+schedule\b", re.IGNORECASE),
    re.compile(r"\bгибрид\b", re.IGNORECASE),
    re.compile(r"\bгибридн\w*\b", re.IGNORECASE),
]

_REMOTE_PATTERNS = [
    re.compile(r"\bremote\b", re.IGNORECASE),
    re.compile(r"\bremote\s+work\b", re.IGNORECASE),
    re.compile(r"\bremote\s+position\b", re.IGNORECASE),
    re.compile(r"\bwork\s+from\s+home\b", re.IGNORECASE),
    re.compile(r"\bfully\s+remote\b", re.IGNORECASE),
    re.compile(r"\b100\s*%\s*remote\b", re.IGNORECASE),
    re.compile(r"\bудалённ\w*\b", re.IGNORECASE),
    re.compile(r"\bудалёнка\b", re.IGNORECASE),
    re.compile(r"\bудаленн\w*\b", re.IGNORECASE),
    re.compile(r"\bудаленка\b", re.IGNORECASE),
    re.compile(r"\bдистанцион\w*\b", re.IGNORECASE),
    re.compile(r"\bиз\s+дома\b", re.IGNORECASE),
    re.compile(r"\bна\s+дому\b", re.IGNORECASE),
]

_OFFICE_PATTERNS = [
    re.compile(r"\boffice\b", re.IGNORECASE),
    re.compile(r"\bon-?site\b", re.IGNORECASE),
    re.compile(r"\bin-?office\b", re.IGNORECASE),
    re.compile(r"\boffice\s+work\b", re.IGNORECASE),
    re.compile(r"\bофис\b", re.IGNORECASE),
    re.compile(r"\bв\s+офисе\b", re.IGNORECASE),
    re.compile(r"\bна\s+месте\b", re.IGNORECASE),
]

_INFORMATIVE_MIN_LENGTH = 20


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip."""
    return " ".join(text.split())


def _first_sentence(text: str) -> str | None:
    """Return the first complete sentence (ending with ``.!?``), or ``None``."""
    match = re.search(r"[.!?](?:\s|$)", text)
    if match is not None:
        return text[: match.end()].rstrip()
    return None


def summarize_vacancy(description_text: str, max_characters: int = 360) -> str:
    """Return a deterministic vacancy summary of at most *max_characters*.

    Normalizes whitespace, inspects the first complete sentence, and returns
    it when it is informative and within the limit.  Otherwise the whole
    normalized text is returned when it fits; if it does not, the text is
    truncated on a word boundary and a single ellipsis is appended.

    Empty descriptions return an empty string.

    Raises :class:`ValueError` when *max_characters* is not positive.
    """
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")

    normalized = _normalize_whitespace(description_text)
    if not normalized:
        return ""

    first = _first_sentence(normalized)
    if (
        first is not None
        and len(first) >= _INFORMATIVE_MIN_LENGTH
        and len(first) <= max_characters
    ):
        return first

    if len(normalized) <= max_characters:
        return normalized

    ellipsis = "\u2026"
    available = max_characters - len(ellipsis)
    if available <= 0:
        return ellipsis[:max_characters]
    truncated = normalized[:available]
    if not normalized[available : available + 1].isspace():
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
    return truncated.rstrip() + ellipsis


def detect_work_format(
    title: str,
    location: str,
    description_text: str,
) -> Literal["remote", "hybrid", "office", "unspecified"]:
    """Classify work format from title, location, and description.

    Hybrid indicators take precedence over remote and office.
    Remote indicators take precedence over office.
    Word and phrase boundaries are used to avoid substring false positives.
    """
    combined = f"{title} {location} {description_text}"
    if any(p.search(combined) for p in _HYBRID_PATTERNS):
        return "hybrid"
    if any(p.search(combined) for p in _REMOTE_PATTERNS):
        return "remote"
    if any(p.search(combined) for p in _OFFICE_PATTERNS):
        return "office"
    return "unspecified"
