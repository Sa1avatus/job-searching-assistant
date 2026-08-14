from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import TaskState, TaskTransition
from app.storage.tables import TaskTransitionRow, WorkflowTaskRow
from app.workflows.task import WorkflowTask


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task_id: str
    application_id: str | None
    idempotency_key: str
    attempt_number: int
    queue_name: str
    payload: dict[str, object]


class SqlTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(
        self,
        idempotency_key: str,
        application_id: str | None = None,
        *,
        queue_name: str = "dispatcher",
        priority: int = 0,
        payload: dict[str, object] | None = None,
    ) -> WorkflowTask:
        row = self._session.scalar(
            select(WorkflowTaskRow).where(WorkflowTaskRow.idempotency_key == idempotency_key)
        )
        if row is None:
            row = WorkflowTaskRow(
                idempotency_key=idempotency_key,
                application_id=application_id,
                queue_name=queue_name,
                priority=priority,
                task_payload=payload or {},
            )
            self._session.add(row)
            self._session.commit()
        return self._to_domain(row)

    def save(self, task: WorkflowTask) -> None:
        row = self._session.scalar(
            select(WorkflowTaskRow).where(WorkflowTaskRow.idempotency_key == task.idempotency_key)
        )
        if row is None:
            row = WorkflowTaskRow(idempotency_key=task.idempotency_key)
            self._session.add(row)
            self._session.flush()
        row.state = task.state.value
        row.attempt_number = task.attempt_number
        existing_count = len(row.transitions)
        for transition in task.transitions[existing_count:]:
            row.transitions.append(
                TaskTransitionRow(
                    previous_state=transition.previous_state.value,
                    new_state=transition.new_state.value,
                    reason=transition.reason,
                    worker=transition.worker,
                    attempt_number=transition.attempt_number,
                    evidence=list(transition.evidence),
                    occurred_at=transition.occurred_at,
                )
            )
        self._session.commit()

    def claim_next(
        self,
        *,
        worker: str,
        queue_name: str = "dispatcher",
        now: datetime | None = None,
    ) -> ClaimedTask | None:
        claim_time = now or datetime.now(UTC)
        row = self._session.scalar(
            select(WorkflowTaskRow)
            .where(
                WorkflowTaskRow.state.in_(
                    (TaskState.SCHEDULED.value, TaskState.RETRY_SCHEDULED.value)
                ),
                WorkflowTaskRow.scheduled_for <= claim_time,
                WorkflowTaskRow.queue_name == queue_name,
            )
            .order_by(WorkflowTaskRow.priority.desc(), WorkflowTaskRow.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            self._session.commit()
            return None
        previous_state = TaskState(row.state)
        row.state = TaskState.RUNNING.value
        row.attempt_number += 1
        row.updated_at = claim_time
        row.transitions.append(
            TaskTransitionRow(
                previous_state=previous_state.value,
                new_state=TaskState.RUNNING.value,
                reason="task claimed from durable queue",
                worker=worker,
                attempt_number=row.attempt_number,
                evidence=[],
                occurred_at=claim_time,
            )
        )
        self._session.commit()
        return ClaimedTask(
            row.id,
            row.application_id,
            row.idempotency_key,
            row.attempt_number,
            row.queue_name,
            row.task_payload,
        )

    def touch_claim(self, task_id: str, *, now: datetime | None = None) -> bool:
        row = self._session.get(WorkflowTaskRow, task_id)
        if row is None or row.state != TaskState.RUNNING.value:
            return False
        row.updated_at = now or datetime.now(UTC)
        self._session.commit()
        return True

    def recover_stale_running_tasks(
        self,
        *,
        cutoff: datetime,
        recovery_worker: str,
        queue_name: str,
        max_attempts: int,
        now: datetime | None = None,
    ) -> int:
        recovery_time = now or datetime.now(UTC)
        rows = self._session.scalars(
            select(WorkflowTaskRow).where(
                WorkflowTaskRow.state == TaskState.RUNNING.value,
                WorkflowTaskRow.updated_at < cutoff,
                WorkflowTaskRow.queue_name == queue_name,
            )
        ).all()
        for row in rows:
            task = self._to_domain(row)
            evidence = (f"stale_before:{cutoff.isoformat()}",)
            transitions: tuple[TaskTransition, ...]
            if task.attempt_number >= max_attempts:
                transitions = (
                    task.transition(
                        TaskState.FAILED,
                        reason="stale task exhausted bounded retry attempts",
                        worker=recovery_worker,
                        evidence=evidence,
                    ),
                )
            else:
                transitions = (
                    task.transition(
                        TaskState.INTERRUPTED,
                        reason="stale running task recovered after worker interruption",
                        worker=recovery_worker,
                        evidence=evidence,
                    ),
                    task.transition(
                        TaskState.SCHEDULED,
                        reason="recovered task returned to its durable queue",
                        worker=recovery_worker,
                        evidence=evidence,
                    ),
                )
                row.scheduled_for = recovery_time
            row.state = task.state.value
            row.updated_at = recovery_time
            for transition in transitions:
                row.transitions.append(
                    TaskTransitionRow(
                        previous_state=transition.previous_state.value,
                        new_state=transition.new_state.value,
                        reason=transition.reason,
                        worker=transition.worker,
                        attempt_number=transition.attempt_number,
                        evidence=list(transition.evidence),
                        occurred_at=transition.occurred_at,
                    )
                )
        self._session.commit()
        return len(rows)

    def finish_claim(
        self,
        task_id: str,
        *,
        new_state: TaskState,
        reason: str,
        worker: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        row = self._session.get(WorkflowTaskRow, task_id)
        if row is None:
            raise LookupError("Workflow task not found")
        task = self._to_domain(row)
        transition = task.transition(
            new_state,
            reason=reason,
            worker=worker,
            evidence=evidence,
        )
        row.state = new_state.value
        row.transitions.append(
            TaskTransitionRow(
                previous_state=transition.previous_state.value,
                new_state=transition.new_state.value,
                reason=transition.reason,
                worker=transition.worker,
                attempt_number=transition.attempt_number,
                evidence=list(transition.evidence),
                occurred_at=transition.occurred_at,
            )
        )
        self._session.commit()

    def reschedule_claim(
        self,
        task_id: str,
        *,
        worker: str,
        reason: str,
        delay_seconds: int,
    ) -> None:
        if delay_seconds < 1:
            raise ValueError("delay_seconds must be positive")
        row = self._session.get(WorkflowTaskRow, task_id)
        if row is None:
            raise LookupError("Workflow task not found")
        task = self._to_domain(row)
        interrupted = task.transition(TaskState.INTERRUPTED, reason=reason, worker=worker)
        scheduled = task.transition(
            TaskState.SCHEDULED,
            reason="task rescheduled after lease conflict",
            worker=worker,
        )
        row.state = TaskState.SCHEDULED.value
        row.scheduled_for = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        for transition in (interrupted, scheduled):
            row.transitions.append(
                TaskTransitionRow(
                    previous_state=transition.previous_state.value,
                    new_state=transition.new_state.value,
                    reason=transition.reason,
                    worker=transition.worker,
                    attempt_number=transition.attempt_number,
                    evidence=list(transition.evidence),
                    occurred_at=transition.occurred_at,
                )
            )
        self._session.commit()

    def retry_claim(
        self,
        task_id: str,
        *,
        worker: str,
        reason: str,
        delay_seconds: int,
        evidence: tuple[str, ...] = (),
        new_priority: int | None = None,
    ) -> None:
        if delay_seconds < 1:
            raise ValueError("delay_seconds must be positive")
        row = self._session.get(WorkflowTaskRow, task_id)
        if row is None:
            raise LookupError("Workflow task not found")
        task = self._to_domain(row)
        transition = task.transition(
            TaskState.RETRY_SCHEDULED,
            reason=reason,
            worker=worker,
            evidence=evidence,
        )
        row.state = TaskState.RETRY_SCHEDULED.value
        row.scheduled_for = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        if new_priority is not None:
            row.priority = new_priority
        row.transitions.append(
            TaskTransitionRow(
                previous_state=transition.previous_state.value,
                new_state=transition.new_state.value,
                reason=transition.reason,
                worker=transition.worker,
                attempt_number=transition.attempt_number,
                evidence=list(transition.evidence),
                occurred_at=transition.occurred_at,
            )
        )
        self._session.commit()

    def interrupt_stale_running_tasks(
        self,
        *,
        cutoff: datetime,
        recovery_worker: str,
        queue_name: str | None = None,
    ) -> int:
        conditions = [
            WorkflowTaskRow.state == TaskState.RUNNING.value,
            WorkflowTaskRow.updated_at < cutoff,
        ]
        if queue_name is not None:
            conditions.append(WorkflowTaskRow.queue_name == queue_name)
        rows = self._session.scalars(select(WorkflowTaskRow).where(*conditions)).all()
        for row in rows:
            task = self._to_domain(row)
            task.transition(
                TaskState.INTERRUPTED,
                reason="stale running task recovered after worker interruption",
                worker=recovery_worker,
                evidence=(f"stale_before:{cutoff.isoformat()}",),
            )
            row.state = task.state.value
            row.transitions.append(
                TaskTransitionRow(
                    previous_state=TaskState.RUNNING.value,
                    new_state=TaskState.INTERRUPTED.value,
                    reason=task.transitions[-1].reason,
                    worker=recovery_worker,
                    attempt_number=task.attempt_number,
                    evidence=list(task.transitions[-1].evidence),
                    occurred_at=task.transitions[-1].occurred_at,
                )
            )
        self._session.commit()
        return len(rows)

    @staticmethod
    def _to_domain(row: WorkflowTaskRow) -> WorkflowTask:
        return WorkflowTask(
            idempotency_key=row.idempotency_key,
            state=TaskState(row.state),
            attempt_number=row.attempt_number,
            transitions=[
                TaskTransition(
                    previous_state=TaskState(transition.previous_state),
                    new_state=TaskState(transition.new_state),
                    reason=transition.reason,
                    worker=transition.worker,
                    attempt_number=transition.attempt_number,
                    evidence=tuple(transition.evidence),
                    occurred_at=transition.occurred_at,
                )
                for transition in row.transitions
            ],
        )
