from __future__ import annotations

import re

from app.domain.application_email import ApplicationEmailOutcome

_REJECTION_PATTERNS = (
    re.compile(r"\b(?:not moving forward|will not be moving forward)\b", re.IGNORECASE),
    re.compile(r"\b(?:other candidates|another candidate)\b", re.IGNORECASE),
    re.compile(r"\b(?:application was unsuccessful|unable to offer you)\b", re.IGNORECASE),
    re.compile(r"\bк сожалению\b", re.IGNORECASE),
    re.compile(r"\bне готовы продолжить\b", re.IGNORECASE),
    re.compile(r"\b(?:отказ|отклонена|не прошли)\b", re.IGNORECASE),
)

_NEXT_STAGE_PATTERNS = (
    re.compile(r"\b(?:next stage|next step)\b", re.IGNORECASE),
    re.compile(r"\b(?:schedule|book) (?:an? )?(?:interview|call)\b", re.IGNORECASE),
    re.compile(r"\binvit(?:e|ed|ation) (?:you )?(?:to|for) (?:an? )?interview\b", re.IGNORECASE),
    re.compile(r"\bследующ(?:ий|ему|его) этап", re.IGNORECASE),
    re.compile(r"\bприглашаем (?:вас )?(?:на|к) (?:интервью|собеседованию)\b", re.IGNORECASE),
    re.compile(r"\bназначить (?:интервью|собеседование|звонок)\b", re.IGNORECASE),
)


def classify_application_email(subject: str, body: str) -> ApplicationEmailOutcome:
    text = " ".join(f"{subject}\n{body}".split())
    has_rejection = any(pattern.search(text) is not None for pattern in _REJECTION_PATTERNS)
    has_next_stage = any(pattern.search(text) is not None for pattern in _NEXT_STAGE_PATTERNS)
    if has_rejection == has_next_stage:
        return ApplicationEmailOutcome.UNKNOWN
    if has_rejection:
        return ApplicationEmailOutcome.REJECTED
    return ApplicationEmailOutcome.NEXT_STAGE
