"""Canonical user-visible application statuses."""

from typing import Literal, get_args

ApplicationStatus = Literal[
    "draft",
    "saved",
    "awaiting_review",
    "approved",
    "rejected",
    "employer_rejected",
    "skipped",
    "submitted",
    "interview",
    "offer",
    "withdrawn",
]

APPLICATION_STATUSES: tuple[ApplicationStatus, ...] = get_args(ApplicationStatus)

TERMINAL_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        "rejected",
        "employer_rejected",
        "skipped",
        "withdrawn",
    }
)

ACTIVE_STATUSES: frozenset[ApplicationStatus] = frozenset(
    set(APPLICATION_STATUSES) - TERMINAL_STATUSES
)
