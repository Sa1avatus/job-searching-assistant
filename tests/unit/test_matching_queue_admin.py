import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import TaskState
from app.matching.queue_admin import (
    clear_matching_queue,
    matching_queue_is_paused,
    set_matching_queue_paused,
)
from app.storage.database import Base
from app.storage.tables import WorkflowTaskRow


def _task(key: str, state: TaskState) -> WorkflowTaskRow:
    return WorkflowTaskRow(
        idempotency_key=f"matching-v2:{key}",
        queue_name="matching",
        state=state.value,
    )


def test_clear_matching_queue_cancels_backlog_and_interrupts_running() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pending = _task("pending", TaskState.PENDING)
        scheduled = _task("scheduled", TaskState.SCHEDULED)
        retry = _task("retry", TaskState.RETRY_SCHEDULED)
        running = _task("running", TaskState.RUNNING)
        done = _task("done", TaskState.COMPLETED)
        for row in (pending, scheduled, retry, running, done):
            session.add(row)
        session.commit()

        report = clear_matching_queue(session)
        session.refresh(pending)
        session.refresh(scheduled)
        session.refresh(retry)
        session.refresh(running)
        session.refresh(done)

        assert report.cancelled == 3
        assert report.interrupted == 1
        assert pending.state == TaskState.CANCELLED.value
        assert scheduled.state == TaskState.CANCELLED.value
        assert retry.state == TaskState.CANCELLED.value
        assert running.state == TaskState.INTERRUPTED.value
        assert done.state == TaskState.COMPLETED.value
        assert running.transitions[-1].worker == "matching-queue-admin"


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    async def get(self, name: str) -> object | None:
        return self._data.get(name)

    async def set(self, name: str, value: str) -> None:
        self._data[name] = value

    async def delete(self, name: str) -> int:
        existed = name in self._data
        self._data.pop(name, None)
        return 1 if existed else 0


def test_matching_queue_pause_flag_round_trip() -> None:
    client = _FakeRedis()

    async def scenario() -> None:
        assert await matching_queue_is_paused(client) is False
        await set_matching_queue_paused(client, paused=True)
        assert await matching_queue_is_paused(client) is True
        # A client with decode_responses=False returns bytes, not str.
        client._data["recruitment:matching:queue:paused"] = b"1"
        assert await matching_queue_is_paused(client) is True
        await set_matching_queue_paused(client, paused=False)
        assert await matching_queue_is_paused(client) is False

    asyncio.run(scenario())
