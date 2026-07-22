from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.storage.database import Base
from app.storage.tables import WorkflowTaskRow
from app.storage.task_repository import SqlTaskRepository
from app.workflows.task import WorkflowTask


def test_task_is_persisted_with_transition_history() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = SqlTaskRepository(session)
        task = WorkflowTask("application:test:1")
        task.transition(TaskState.SCHEDULED, reason="queued", worker="api")
        task.transition(TaskState.RUNNING, reason="claimed", worker="worker")
        repository.save(task)

        restored = repository.get_or_create("application:test:1")

        assert restored.state is TaskState.RUNNING
        assert len(restored.transitions) == 2
        assert restored.attempt_number == 1


def test_stale_running_task_is_interrupted_for_safe_recovery() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = SqlTaskRepository(session)
        task = WorkflowTask("application:stale:1")
        task.transition(TaskState.SCHEDULED, reason="queued", worker="api")
        task.transition(TaskState.RUNNING, reason="claimed", worker="dead-worker")
        repository.save(task)
        row = session.query(WorkflowTaskRow).filter_by(idempotency_key=task.idempotency_key).one()
        row.updated_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()

        recovered_count = repository.interrupt_stale_running_tasks(
            cutoff=datetime.now(UTC) - timedelta(minutes=5),
            recovery_worker="recovery-1",
        )
        restored = repository.get_or_create(task.idempotency_key)

        assert recovered_count == 1
        assert restored.state is TaskState.INTERRUPTED
        assert restored.transitions[-1].worker == "recovery-1"
