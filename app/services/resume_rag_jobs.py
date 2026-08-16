from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.storage.tables import CvFileRow, TaskTransitionRow, WorkflowTaskRow
from app.storage.task_repository import SqlTaskRepository

_TASK_KIND = "rag-resume-sync"
_ACTIVE_STATES = {
    TaskState.PENDING.value,
    TaskState.SCHEDULED.value,
    TaskState.RUNNING.value,
    TaskState.RETRY_SCHEDULED.value,
}


class ResumeRagJobNotReadyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResumeRagJobStatus:
    task_id: str | None
    status: str
    attempt_number: int
    failure_code: str | None
    updated_at: datetime | None
    synced_at: datetime | None


class ResumeRagJobService:
    """Schedule resume indexing without copying resume content into queue payloads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def schedule(self, cv_file_id: str, *, force: bool = False) -> WorkflowTaskRow:
        cv_file = self._session.get(CvFileRow, cv_file_id)
        if cv_file is None:
            raise LookupError("CV file not found")
        if cv_file.analyzed_at is None:
            raise ResumeRagJobNotReadyError("Confirm the resume profile before sending it to RAG")

        base_key = self._base_key(cv_file)
        tasks = self._tasks_for_cv(cv_file.id)
        active = next((task for task in tasks if task.state in _ACTIVE_STATES), None)
        if active is not None:
            return active

        idempotency_key = base_key
        if force and tasks:
            idempotency_key = f"{base_key}:retry-{len(tasks)}"

        repository = SqlTaskRepository(self._session)
        workflow_task = repository.get_or_create(
            idempotency_key,
            payload={
                "cv_file_id": cv_file.id,
                "content_version": base_key.rsplit(":", 1)[-1],
            },
        )
        if workflow_task.state is TaskState.PENDING:
            workflow_task.transition(
                TaskState.SCHEDULED,
                reason="resume RAG synchronization requested",
                worker="rag-resume-scheduler",
            )
            repository.save(workflow_task)

        task = self._session.scalar(
            select(WorkflowTaskRow).where(WorkflowTaskRow.idempotency_key == idempotency_key)
        )
        if task is None:
            raise RuntimeError("Resume RAG synchronization task was not persisted")
        return task

    def status(self, cv_file_id: str) -> ResumeRagJobStatus:
        tasks = self._tasks_for_cv(cv_file_id)
        if not tasks:
            return ResumeRagJobStatus(None, "not_scheduled", 0, None, None, None)
        task = tasks[0]
        failure_code = None
        if task.state in {TaskState.FAILED.value, TaskState.RETRY_SCHEDULED.value}:
            transitions = self._session.scalars(
                select(TaskTransitionRow)
                .where(TaskTransitionRow.task_id == task.id)
                .order_by(TaskTransitionRow.occurred_at.desc(), TaskTransitionRow.id.desc())
            ).all()
            failure_code = next(
                (
                    item.removeprefix("failure_code:")
                    for transition in transitions
                    for item in transition.evidence
                    if item.startswith("failure_code:")
                ),
                None,
            )
        return ResumeRagJobStatus(
            task_id=task.id,
            status=task.state,
            attempt_number=task.attempt_number,
            failure_code=failure_code,
            updated_at=task.updated_at,
            synced_at=task.updated_at if task.state == TaskState.COMPLETED.value else None,
        )

    def delete_for_cv(self, cv_file_id: str) -> None:
        for task in self._tasks_for_cv(cv_file_id):
            self._session.delete(task)
        self._session.commit()

    def _tasks_for_cv(self, cv_file_id: str) -> list[WorkflowTaskRow]:
        prefix = f"{_TASK_KIND}:{cv_file_id}:"
        return list(
            self._session.scalars(
                select(WorkflowTaskRow)
                .where(WorkflowTaskRow.idempotency_key.startswith(prefix))
                .order_by(WorkflowTaskRow.created_at.desc(), WorkflowTaskRow.id.desc())
            ).all()
        )

    @staticmethod
    def _base_key(cv_file: CvFileRow) -> str:
        content = json.dumps(
            {
                "sha256": cv_file.sha256,
                "analyzed_at": cv_file.analyzed_at.isoformat() if cv_file.analyzed_at else None,
                "skills": cv_file.skills,
                "experience_summary": cv_file.experience_summary,
                "search_keywords": cv_file.search_keywords,
                "years_of_experience": cv_file.years_of_experience,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        version = hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]
        return f"{_TASK_KIND}:{cv_file.id}:{version}"
