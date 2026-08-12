from __future__ import annotations

import hashlib

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    CvFileRow,
    RequirementMatchRow,
    TaskTransitionRow,
    VacancyRow,
    WorkflowTaskRow,
)
from app.storage.task_repository import SqlTaskRepository


class MatchingJobNotReadyError(ValueError):
    pass


class MatchingJobService:
    """Schedule idempotent matching work without placing CV text in the queue payload."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def schedule(self, application_id: str, *, force: bool = False) -> WorkflowTaskRow:
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

        content_version = hashlib.sha256(
            "\n".join(
                (
                    vacancy.description_text,
                    cv_file.sha256,
                    cv_file.analyzed_at.isoformat() if cv_file.analyzed_at else "not-analyzed",
                )
            ).encode("utf-8")
        ).hexdigest()[:20]
        idempotency_key = f"matching-v2:{application.id}:{content_version}"
        repository = SqlTaskRepository(self._session)

        # Force re-run: delete old task, results and requirement matches
        if force:
            self._session.execute(
                delete(RequirementMatchRow).where(
                    RequirementMatchRow.application_id == application.id
                )
            )
            self._session.execute(
                delete(ApplicationMatchResultRow).where(
                    ApplicationMatchResultRow.application_id == application.id
                )
            )
            existing = self._session.scalar(
                select(WorkflowTaskRow).where(
                    WorkflowTaskRow.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                self._session.execute(
                    delete(TaskTransitionRow).where(
                        TaskTransitionRow.task_id == existing.id
                    )
                )
                self._session.delete(existing)
                self._session.flush()

        workflow_task = repository.get_or_create(
            idempotency_key,
            application.id,
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
            select(WorkflowTaskRow).where(
                WorkflowTaskRow.idempotency_key == idempotency_key
            )
        )
        if task is None:
            raise RuntimeError("Matching task was not persisted")
        if task.state in {"pending", "scheduled", "retry_scheduled", "running"}:
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
