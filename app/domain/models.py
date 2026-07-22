from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class SubmissionMode(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    AUTOMATIC = "automatic"


class TaskState(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_EXTERNAL_SYSTEM = "waiting_for_external_system"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class ProfileFact:
    category: str
    name: str
    value: str
    is_verified: bool = True


@dataclass(frozen=True, slots=True)
class Vacancy:
    source_url: str
    title: str
    company: str
    required_skills: frozenset[str] = frozenset()
    preferred_skills: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MatchAssessment:
    score: int
    matched_required_skills: tuple[str, ...]
    missing_required_skills: tuple[str, ...]
    matched_preferred_skills: tuple[str, ...]
    recommendation: str


@dataclass(frozen=True, slots=True)
class ApplicationQuestion:
    field_id: str
    label: str
    semantic_category: str
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class PreparedAnswer:
    field_id: str
    answer: str | None
    source_fact_name: str | None
    requires_review: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class TaskTransition:
    previous_state: TaskState
    new_state: TaskState
    reason: str
    worker: str
    attempt_number: int
    evidence: tuple[str, ...] = ()
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        return {
            "previous_state": self.previous_state.value,
            "new_state": self.new_state.value,
            "reason": self.reason,
            "worker": self.worker,
            "attempt_number": self.attempt_number,
            "evidence": list(self.evidence),
            "occurred_at": self.occurred_at.isoformat(),
        }
