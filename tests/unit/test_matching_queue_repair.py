from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.matching.queue_repair import repair_legacy_matching_queue
from app.storage.database import Base
from app.storage.tables import WorkflowTaskRow


def test_queue_repair_migrates_latest_task_and_preserves_history() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        old_retry = WorkflowTaskRow(
            idempotency_key="matching-v2:old:1",
            queue_name="dispatcher",
            state=TaskState.RETRY_SCHEDULED.value,
        )
        latest = WorkflowTaskRow(
            idempotency_key="matching-v2:latest:1",
            queue_name="dispatcher",
            state=TaskState.SCHEDULED.value,
        )
        session.add(old_retry)
        session.flush()
        old_retry.created_at = old_retry.created_at.replace(year=2020)
        session.add(latest)
        session.commit()

        dry_run = repair_legacy_matching_queue(session, apply=False)
        assert dry_run.active_legacy_tasks == 2
        assert dry_run.migrated == 1
        assert dry_run.cancelled == 1

        applied = repair_legacy_matching_queue(session, apply=True)
        session.refresh(old_retry)
        session.refresh(latest)

        assert applied.kept_task_id == latest.id
        assert latest.queue_name == "matching"
        assert latest.priority == 100
        assert latest.state == TaskState.SCHEDULED.value
        assert old_retry.state == TaskState.CANCELLED.value
        assert old_retry.transitions[-1].worker == "matching-queue-repair"
