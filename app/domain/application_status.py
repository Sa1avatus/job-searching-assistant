"""Canonical user-visible application statuses."""

from typing import Literal, get_args

ApplicationStatus = Literal[
    "draft",
    "saved",
    "awaiting_review",
    "approved",
    "rejected",
    "skipped",
    "submitted",
    "interview",
]

APPLICATION_STATUSES: tuple[ApplicationStatus, ...] = get_args(ApplicationStatus)
