from __future__ import annotations

import re

from app.domain.application_email import (
    ApplicationEmailOutcome,
    EmailCategory,
)

_REJECTION_PATTERNS = (
    re.compile(r"\b(?:not moving forward|will not be moving forward)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:will not|won't|cannot|can't|unable to|not able to)\s+"
        r"(?:move forward|proceed|progress|continue)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:decided|chosen|elected)\s+(?:not to|to not)\s+"
        r"(?:move forward|proceed|progress|continue)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:other candidates|another candidate)\b", re.IGNORECASE),
    re.compile(r"\b(?:application was unsuccessful|unable to offer you)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:application|candidacy|profile)\s+(?:has\s+)?(?:not been|was not|is not)\s+"
        r"(?:selected|successful|shortlisted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:not selected|unsuccessful)\s+(?:for|in)\s+"
        r"(?:this|the|our)\s+(?:position|role|vacancy)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:pursu(?:e|ing)|proceed(?:ing)?|mov(?:e|ing) forward)\s+with\s+"
        r"(?:other|another)\s+candidate",
        re.IGNORECASE,
    ),
    re.compile(r"\bregret to inform you\b", re.IGNORECASE),
    re.compile(
        r"\b(?:your )?(?:skill ?set|experience|profile) does not match "
        r"(?:our |the )?(?:qualifications?|requirements?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:you|your (?:skill ?set|experience|profile)) do(?:es)? not meet "
        r"(?:our |the )?(?:qualifications?|requirements?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bк сожалению\b", re.IGNORECASE),
    re.compile(r"\bне готовы продолжить\b", re.IGNORECASE),
    re.compile(r"\b(?:отказ|отклонена|не прошли)\b", re.IGNORECASE),
)

_NEXT_STAGE_PATTERNS = (
    re.compile(r"\b(?:next stage|next step)\b", re.IGNORECASE),
    re.compile(r"\b(?:schedule|book) (?:an? )?(?:interview|call)\b", re.IGNORECASE),
    re.compile(r"\binvit(?:e|ed|ation) (?:you )?(?:to|for) (?:an? ?)interview\b", re.IGNORECASE),
    re.compile(r"\bследующ(?:ий|ему|его) этап", re.IGNORECASE),
    re.compile(r"\bприглашаем (?:вас )?(?:на|к) (?:интервью|собеседованию)\b", re.IGNORECASE),
    re.compile(r"\bназначить (?:интервью|собеседование|звонок)\b", re.IGNORECASE),
)

_CATEGORY_PATTERNS: dict[EmailCategory, tuple[re.Pattern[str], ...]] = {
    EmailCategory.REJECTION: _REJECTION_PATTERNS,
    EmailCategory.INTERVIEW_INVITATION: (
        re.compile(
            r"\binvit(?:e|ed|ation) (?:you )?(?:to|for) (?:an? ?)interview\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:schedule|book) (?:an? )?(?:interview|call)\b", re.IGNORECASE),
        re.compile(
            r"\bприглашаем (?:вас )?(?:на|к) (?:интервью|собеседованию)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bназначить (?:интервью|собеседование|звонок)\b", re.IGNORECASE),
    ),
    EmailCategory.INTERVIEW_RESCHEDULE: (
        re.compile(r"\breschedul(?:e|ing)\b", re.IGNORECASE),
        re.compile(r"\bперенос(?:им|е)\b", re.IGNORECASE),
    ),
    EmailCategory.TEST_ASSIGNMENT: (
        re.compile(r"\b(?:test|coding) (?:assignment|challenge|task)\b", re.IGNORECASE),
        re.compile(r"\bтестов(?:ое|ая|ый) задан(?:ие|ие)\b", re.IGNORECASE),
    ),
    EmailCategory.OFFER: (
        re.compile(r"\b(?:offer|offer letter|compensation package)\b", re.IGNORECASE),
        re.compile(r"\bпредлагаем (?:вам )?(?:позицию|должность)\b", re.IGNORECASE),
    ),
    EmailCategory.APPLICATION_RECEIVED: (
        re.compile(
            r"\b(?:application (?:has been )?received"
            r"|thank you for (?:your )?applying)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:ваше резюме получено|заявка принята)\b", re.IGNORECASE),
    ),
    EmailCategory.RECRUITER_CONTACT: (
        re.compile(r"\b(?:sourcing|talent acquisition|recruiting team)\b", re.IGNORECASE),
        re.compile(r"\b(?:заинтересованы в вашем профиле|подбор персонала)\b", re.IGNORECASE),
    ),
    EmailCategory.QUESTION: (
        re.compile(
            r"\b(?:additional questions?"
            r"|could you (?:clarify|confirm|provide))\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:уточн(?:ите|яющ)|дополнительн(?:ые|ый) вопрос)\b",
            re.IGNORECASE,
        ),
    ),
}


def classify_email_category(subject: str, body: str) -> EmailCategory:
    text = " ".join(f"{subject}\n{body}".split())
    for category, patterns in _CATEGORY_PATTERNS.items():
        if any(pattern.search(text) is not None for pattern in patterns):
            return category
    return EmailCategory.OTHER


def classify_application_email(subject: str, body: str) -> ApplicationEmailOutcome:
    text = " ".join(f"{subject}\n{body}".split())
    has_rejection = any(pattern.search(text) is not None for pattern in _REJECTION_PATTERNS)
    has_next_stage = any(pattern.search(text) is not None for pattern in _NEXT_STAGE_PATTERNS)
    if has_rejection == has_next_stage:
        return ApplicationEmailOutcome.UNKNOWN
    if has_rejection:
        return ApplicationEmailOutcome.REJECTED
    return ApplicationEmailOutcome.NEXT_STAGE
