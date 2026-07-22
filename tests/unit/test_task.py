import pytest

from app.domain.models import TaskState
from app.workflows.task import InvalidTaskTransition, WorkflowTask


def test_task_records_attempt_and_evidence() -> None:
    task = WorkflowTask(idempotency_key="application:example:42")
    task.transition(TaskState.SCHEDULED, reason="queued", worker="api")
    transition = task.transition(
        TaskState.RUNNING,
        reason="claimed",
        worker="worker-1",
        evidence=("lease:123",),
    )

    assert task.attempt_number == 1
    assert transition.attempt_number == 1
    assert transition.evidence == ("lease:123",)


def test_completed_task_cannot_restart() -> None:
    task = WorkflowTask(idempotency_key="application:example:43")
    task.transition(TaskState.SCHEDULED, reason="queued", worker="api")
    task.transition(TaskState.RUNNING, reason="claimed", worker="worker-1")
    task.transition(TaskState.COMPLETED, reason="verified", worker="worker-1")

    with pytest.raises(InvalidTaskTransition):
        task.transition(TaskState.SCHEDULED, reason="duplicate", worker="api")
