from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import TaskState, TaskTransition

ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.SCHEDULED, TaskState.CANCELLED}),
    TaskState.SCHEDULED: frozenset({TaskState.RUNNING, TaskState.COMPLETED, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.INTERRUPTED,
            TaskState.RETRY_SCHEDULED,
            TaskState.WAITING_FOR_EXTERNAL_SYSTEM,
            TaskState.WAITING_FOR_USER,
        }
    ),
    TaskState.WAITING_FOR_USER: frozenset(
        {TaskState.SCHEDULED, TaskState.COMPLETED, TaskState.CANCELLED}
    ),
    TaskState.WAITING_FOR_EXTERNAL_SYSTEM: frozenset(
        {TaskState.SCHEDULED, TaskState.RETRY_SCHEDULED, TaskState.CANCELLED}
    ),
    TaskState.RETRY_SCHEDULED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.INTERRUPTED: frozenset({TaskState.SCHEDULED, TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset({TaskState.SCHEDULED}),
    TaskState.CANCELLED: frozenset({TaskState.SCHEDULED}),
}


class InvalidTaskTransition(ValueError):
    """Raised when a workflow attempts an invalid state transition."""


@dataclass(slots=True)
class WorkflowTask:
    idempotency_key: str
    state: TaskState = TaskState.PENDING
    attempt_number: int = 0
    transitions: list[TaskTransition] = field(default_factory=list)

    def transition(
        self,
        new_state: TaskState,
        *,
        reason: str,
        worker: str,
        evidence: tuple[str, ...] = (),
    ) -> TaskTransition:
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTaskTransition(f"Cannot transition from {self.state} to {new_state}")
        previous_state = self.state
        if new_state is TaskState.RUNNING:
            self.attempt_number += 1
        transition = TaskTransition(
            previous_state=previous_state,
            new_state=new_state,
            reason=reason,
            worker=worker,
            attempt_number=self.attempt_number,
            evidence=evidence,
        )
        self.state = new_state
        self.transitions.append(transition)
        return transition
