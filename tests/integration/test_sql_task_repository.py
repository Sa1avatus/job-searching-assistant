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


def test_recovery_reschedules_only_stale_tasks_from_selected_queue() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            (
                WorkflowTaskRow(
                    idempotency_key="matching-v2:stale:1",
                    queue_name="matching",
                    state="scheduled",
                ),
                WorkflowTaskRow(
                    idempotency_key="application-review:stale:1",
                    queue_name="dispatcher",
                    state="scheduled",
                ),
            )
        )
        session.commit()
        repository = SqlTaskRepository(session)
        matching_claim = repository.claim_next(worker="matching-1", queue_name="matching")
        dispatcher_claim = repository.claim_next(worker="dispatcher-1", queue_name="dispatcher")
        assert matching_claim is not None
        assert dispatcher_claim is not None
        cutoff = datetime.now(UTC) - timedelta(minutes=5)
        for task_id in (matching_claim.task_id, dispatcher_claim.task_id):
            row = session.get(WorkflowTaskRow, task_id)
            assert row is not None
            row.updated_at = cutoff - timedelta(minutes=1)
        session.commit()

        recovered = repository.recover_stale_running_tasks(
            cutoff=cutoff,
            recovery_worker="matching-recovery",
            queue_name="matching",
            max_attempts=3,
        )

        matching = session.get(WorkflowTaskRow, matching_claim.task_id)
        dispatcher = session.get(WorkflowTaskRow, dispatcher_claim.task_id)
        assert recovered == 1
        assert matching is not None and matching.state == TaskState.SCHEDULED.value
        assert [transition.new_state for transition in matching.transitions[-2:]] == [
            TaskState.INTERRUPTED.value,
            TaskState.SCHEDULED.value,
        ]
        assert dispatcher is not None and dispatcher.state == TaskState.RUNNING.value
