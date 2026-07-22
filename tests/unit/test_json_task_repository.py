from pathlib import Path

from app.domain.models import TaskState
from app.storage.json_task_repository import JsonTaskRepository
from app.workflows.task import WorkflowTask


def test_task_survives_repository_reload(tmp_path: Path) -> None:
    repository = JsonTaskRepository(tmp_path / "tasks" / "application-42.json")
    task = WorkflowTask(idempotency_key="application:example:42")
    task.transition(TaskState.SCHEDULED, reason="queued", worker="api")
    task.transition(TaskState.RUNNING, reason="claimed", worker="worker-1")
    task.transition(
        TaskState.WAITING_FOR_USER,
        reason="sensitive declaration",
        worker="worker-1",
        evidence=("screenshot:42.png",),
    )

    repository.save(task)
    restored_task = repository.load()

    assert restored_task.idempotency_key == task.idempotency_key
    assert restored_task.state is TaskState.WAITING_FOR_USER
    assert restored_task.attempt_number == 1
    assert restored_task.transitions[-1].evidence == ("screenshot:42.png",)
