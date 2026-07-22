import asyncio
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.domain.models import TaskState
from app.services.recruitment import RecruitmentService
from app.storage.database import Base
from app.storage.tables import WorkerHeartbeatRow, WorkflowTaskRow
from app.storage.task_repository import SqlTaskRepository
from app.workers.coordination import RedisCoordinator
from app.workers.dispatcher import (
    ApplicationReviewCheckpointHandler,
    DurableTaskDispatcher,
    ExecutionOutcome,
    HumanActionRequest,
)


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, *, nx: bool, ex: int) -> bool | None:
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, expected_value = keys_and_args
        if self.values.get(key) != expected_value:
            return 0
        del self.values[key]
        return 1

    async def incr(self, name: str) -> int:
        return 1

    async def expire(self, name: str, seconds: int) -> bool:
        return True


class RetryHandler:
    async def handle(self, _claimed_task: object) -> ExecutionOutcome:
        return ExecutionOutcome(TaskState.RETRY_SCHEDULED, "controlled transient failure")


class EvidenceHandler:
    def __init__(self, screenshot_path: Path) -> None:
        self._screenshot_path = screenshot_path

    async def handle(self, _claimed_task: object) -> ExecutionOutcome:
        return ExecutionOutcome(
            TaskState.WAITING_FOR_USER,
            "controlled evidence ready",
            ("submission:false",),
            HumanActionRequest(
                kind="application_review",
                instructions="Review controlled evidence.",
                screenshot_path=self._screenshot_path,
            ),
        )


def _controlled_png() -> bytes:
    def chunk(chunk_type: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x20\x70\xc0\xff")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_repository_claims_highest_priority_task_only_once() -> None:
    session_factory = _session_factory()
    with session_factory() as session:
        session.add_all(
            [
                WorkflowTaskRow(idempotency_key="low:1", state="scheduled", priority=1),
                WorkflowTaskRow(idempotency_key="high:1", state="scheduled", priority=10),
                WorkflowTaskRow(
                    idempotency_key="browser-review:1",
                    queue_name="browser",
                    task_payload={"workflow": "controlled_review", "fixture_name": "application"},
                    state="scheduled",
                    priority=100,
                ),
            ]
        )
        session.commit()

    with session_factory() as session:
        first = SqlTaskRepository(session).claim_next(worker="worker-1")
    with session_factory() as session:
        second = SqlTaskRepository(session).claim_next(worker="worker-2")
        third = SqlTaskRepository(session).claim_next(worker="worker-3")
        browser_claim = SqlTaskRepository(session).claim_next(
            worker="browser-worker", queue_name="browser"
        )

    assert first is not None and first.idempotency_key == "high:1"
    assert second is not None and second.idempotency_key == "low:1"
    assert third is None
    assert browser_claim is not None
    assert browser_claim.idempotency_key == "browser-review:1"
    assert browser_claim.queue_name == "browser"
    assert browser_claim.payload == {
        "workflow": "controlled_review",
        "fixture_name": "application",
    }


def test_dispatcher_moves_prepared_application_to_durable_review_checkpoint() -> None:
    async def run_test() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            service = RecruitmentService(session)
            user = service.create_user("Candidate")
            vacancy = service.create_vacancy(
                source_url="https://example.test/jobs/dispatcher",
                title="Engineer",
                company="Example",
                required_skills=[],
                preferred_skills=[],
            )
            application = service.prepare_application(user.id, vacancy.id)

        redis_client = FakeRedisClient()
        dispatcher = DurableTaskDispatcher(
            session_factory=session_factory,
            coordinator=RedisCoordinator(redis_client),
            handlers={"application-review": ApplicationReviewCheckpointHandler(session_factory)},
            worker_name="dispatcher-test",
            settings=Settings(
                _env_file=None,
                database_url="sqlite://",
                worker_lease_seconds=30,
                worker_retry_seconds=1,
            ),
        )

        assert await dispatcher.run_once() is True
        assert await dispatcher.run_once() is False

        with session_factory() as session:
            task = session.scalar(
                select(WorkflowTaskRow).where(WorkflowTaskRow.application_id == application.id)
            )
            heartbeat = session.get(WorkerHeartbeatRow, "dispatcher-test")
            assert task is not None
            assert task.state == TaskState.WAITING_FOR_USER.value
            assert [transition.new_state for transition in task.transitions] == [
                TaskState.SCHEDULED.value,
                TaskState.RUNNING.value,
                TaskState.WAITING_FOR_USER.value,
            ]
            assert heartbeat is not None
        assert redis_client.values == {}

        with session_factory() as session:
            RecruitmentService(session).decide_application(application.id, "reject")
        with session_factory() as session:
            task = session.scalar(
                select(WorkflowTaskRow).where(WorkflowTaskRow.application_id == application.id)
            )
            assert task is not None
            assert task.state == TaskState.COMPLETED.value
            assert task.transitions[-1].evidence == [
                f"application:{application.id}",
                "decision:reject",
            ]

    asyncio.run(run_test())


def test_dispatcher_schedules_bounded_handler_retries() -> None:
    async def run_test() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            task = WorkflowTaskRow(idempotency_key="retry:1", state="scheduled")
            session.add(task)
            session.commit()
            task_id = task.id
        dispatcher = DurableTaskDispatcher(
            session_factory=session_factory,
            coordinator=RedisCoordinator(FakeRedisClient()),
            handlers={"retry": RetryHandler()},
            worker_name="retry-worker",
            settings=Settings(
                _env_file=None,
                database_url="sqlite://",
                worker_lease_seconds=30,
                worker_retry_seconds=1,
                worker_max_attempts=2,
            ),
        )

        assert await dispatcher.run_once() is True
        with session_factory() as session:
            task = session.get(WorkflowTaskRow, task_id)
            assert task is not None
            assert task.state == TaskState.RETRY_SCHEDULED.value
            task.scheduled_for = datetime.now(UTC)
            session.commit()

        assert await dispatcher.run_once() is True
        with session_factory() as session:
            task = session.get(WorkflowTaskRow, task_id)
            assert task is not None
            assert task.state == TaskState.FAILED.value
            assert task.attempt_number == 2

    asyncio.run(run_test())


def test_dispatcher_persists_review_checkpoint_and_screenshot_atomically(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        session_factory = _session_factory()
        screenshot_path = tmp_path / "review.png"
        screenshot_path.write_bytes(_controlled_png())
        with session_factory() as session:
            task = WorkflowTaskRow(idempotency_key="evidence:1", state="scheduled")
            session.add(task)
            session.commit()
            task_id = task.id
        dispatcher = DurableTaskDispatcher(
            session_factory=session_factory,
            coordinator=RedisCoordinator(FakeRedisClient()),
            handlers={"evidence": EvidenceHandler(screenshot_path)},
            worker_name="evidence-worker",
            settings=Settings(
                _env_file=None,
                artifact_directory=tmp_path,
                database_url="sqlite://",
                worker_lease_seconds=30,
                max_evidence_bytes=1024,
            ),
        )

        assert await dispatcher.run_once() is True
        with session_factory() as session:
            task = session.get(WorkflowTaskRow, task_id)
            assert task is not None
            assert task.state == TaskState.WAITING_FOR_USER.value
            assert len(task.human_actions) == 1
            checkpoint = task.human_actions[0]
            assert checkpoint.kind == "application_review"
            assert len(checkpoint.artifacts) == 1
            assert checkpoint.artifacts[0].sha256
            assert f"checkpoint:{checkpoint.id}" in task.transitions[-1].evidence
            assert f"artifact:{checkpoint.artifacts[0].id}" in task.transitions[-1].evidence

    asyncio.run(run_test())
