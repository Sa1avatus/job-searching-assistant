from __future__ import annotations

import hashlib

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.models import TaskState
from app.llm.preferences import resolve_model_identity
from app.matching.vacancy_source import build_vacancy_matching_source
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    RequirementMatchRow,
    TaskTransitionRow,
    VacancyRequirementRow,
    VacancyRow,
    WorkflowTaskRow,
)
from app.storage.task_repository import SqlTaskRepository

MATCHING_QUEUE_NAME = "matching"
INTERACTIVE_MATCHING_PRIORITY = 100
BACKGROUND_MATCHING_PRIORITY = 50
RETRY_MATCHING_PRIORITY = 20
BACKFILL_MATCHING_PRIORITY = 10

_ACTIVE_TASK_STATES = {
    TaskState.PENDING.value,
    TaskState.SCHEDULED.value,
    TaskState.RETRY_SCHEDULED.value,
    TaskState.RUNNING.value,
}


def retry_delay_seconds(attempt_number: int, base_seconds: int = 30) -> int:
    """Exponential backoff capped at 5 minutes: 30, 60, 120, 240, 300."""
    return min(base_seconds * (2 ** max(attempt_number - 1, 0)), 300)


class MatchingJobNotReadyError(ValueError):
    pass


class MatchingJobService:
    """Schedule idempotent matching work without placing CV text in the queue payload."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def schedule(
        self,
        application_id: str,
        *,
        force: bool = False,
        priority: int = INTERACTIVE_MATCHING_PRIORITY,
    ) -> WorkflowTaskRow:
        application = self._session.get(ApplicationRow, application_id)
        if application is None:
            raise LookupError("Application not found")
        if application.selected_cv_file_id is None:
            raise MatchingJobNotReadyError("Select a CV before calculating the match")
        cv_file = self._session.get(CvFileRow, application.selected_cv_file_id)
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if cv_file is None or cv_file.user_id != application.user_id:
            raise MatchingJobNotReadyError("Selected CV is unavailable")
        if vacancy is None:
            raise LookupError("Vacancy not found")

        vacancy_source = build_vacancy_matching_source(vacancy)
        # Include the resolved LLM model in the content version so that switching the
        # model invalidates the idempotency key and triggers re-extraction/re-evaluation
        # even on a smart recalculation (which reuses work for unchanged content).
        settings = get_settings()
        encryption_key = (
            settings.browser_state_encryption_key.get_secret_value()
            if settings.browser_state_encryption_key is not None
            else None
        )
        model_identity = resolve_model_identity(self._session, application.user_id, encryption_key)
        content_version = hashlib.sha256(
            "\n".join(
                (
                    vacancy_source,
                    cv_file.sha256,
                    cv_file.analyzed_at.isoformat() if cv_file.analyzed_at else "not-analyzed",
                    model_identity or "",
                )
            ).encode("utf-8")
        ).hexdigest()[:20]
        idempotency_key = f"matching-v2:{application.id}:{content_version}"
        repository = SqlTaskRepository(self._session)

        existing = self._session.scalar(
            select(WorkflowTaskRow).where(WorkflowTaskRow.idempotency_key == idempotency_key)
        )
        if existing is not None and existing.state in _ACTIVE_TASK_STATES:
            if existing.queue_name == MATCHING_QUEUE_NAME and priority > existing.priority:
                existing.priority = priority
            if force:
                existing.refresh_requested = True
                # Clear cached extraction so pipeline re-extracts requirements
                self._clear_extraction_cache(vacancy.id)
            self._session.commit()
            return existing

        # A forced rerun replaces only a terminal task. Active work is coalesced above so a
        # second click cannot delete a claim that a worker is currently executing.
        if force and existing is not None:
            self._clear_extraction_cache(vacancy.id)
            self._session.execute(
                delete(TaskTransitionRow).where(TaskTransitionRow.task_id == existing.id)
            )
            self._session.delete(existing)
            self._session.flush()
        elif existing is not None:
            # Smart recalculation with unchanged content (and model). A completed result is
            # already correct — return it immediately. A terminal-but-not-completed task is
            # replaced with a fresh attempt while KEEPING extraction rows and the persistent
            # LLM cache, so the retry is fast but still re-runs the scoring work.
            if existing.state == TaskState.COMPLETED.value:
                self._session.commit()
                return existing
            self._session.execute(
                delete(TaskTransitionRow).where(TaskTransitionRow.task_id == existing.id)
            )
            self._session.delete(existing)
            self._session.flush()

        workflow_task = repository.get_or_create(
            idempotency_key,
            application.id,
            queue_name=MATCHING_QUEUE_NAME,
            priority=priority,
            payload={
                "application_id": application.id,
                "cv_file_id": cv_file.id,
                "content_version": content_version,
            },
        )
        if workflow_task.state is TaskState.PENDING:
            workflow_task.transition(
                TaskState.SCHEDULED,
                reason="matching v2 calculation requested",
                worker="matching-scheduler",
            )
            repository.save(workflow_task)
        task = self._session.scalar(
            select(WorkflowTaskRow).where(WorkflowTaskRow.idempotency_key == idempotency_key)
        )
        if task is None:
            raise RuntimeError("Matching task was not persisted")
        if task.state in _ACTIVE_TASK_STATES:
            aggregate = self._session.get(ApplicationMatchResultRow, application.id)
            if aggregate is None:
                aggregate = ApplicationMatchResultRow(application_id=application.id)
                self._session.add(aggregate)
            aggregate.cv_file_id = cv_file.id
            aggregate.status = "pending"
            aggregate.failure_reason = None
            aggregate.fallback_reason = None
            self._session.commit()
        return task

    def _clear_extraction_cache(self, vacancy_id: str) -> None:
        """Clear cached extraction results so pipeline re-extracts from scratch."""
        self._session.execute(
            delete(VacancyRequirementRow).where(VacancyRequirementRow.vacancy_id == vacancy_id)
        )
        self._session.execute(
            delete(RequirementMatchRow).where(
                RequirementMatchRow.application_id.in_(
                    select(ApplicationMatchResultRow.application_id).where(
                        ApplicationMatchResultRow.application_id.in_(
                            select(ApplicationRow.id).where(ApplicationRow.vacancy_id == vacancy_id)
                        )
                    )
                )
            )
        )
