from __future__ import annotations

import asyncio
import signal
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import redis.asyncio as redis
import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.domain.models import TaskState
from app.observability.logging import configure_logging
from app.services.rag_sync import RagSyncService
from app.storage.database import SessionFactory
from app.storage.evidence_artifacts import EvidenceArtifactStorage
from app.storage.tables import (
    ApplicationRow,
    CvFileRow,
    HumanActionCheckpointRow,
    WorkerHeartbeatRow,
)
from app.storage.task_repository import ClaimedTask, SqlTaskRepository
from app.workers.coordination import RedisCoordinationClient, RedisCoordinator


@dataclass(frozen=True, slots=True)
class HumanActionRequest:
    kind: str
    instructions: str
    screenshot_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    state: TaskState
    reason: str
    evidence: tuple[str, ...] = ()
    human_action: HumanActionRequest | None = None


class TaskHandler(Protocol):
    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome: ...


class MatchingRunner(Protocol):
    async def run(self, application_id: str) -> None: ...


class MatchingTaskHandler:
    def __init__(self, runner: MatchingRunner) -> None:
        self._runner = runner

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        if claimed_task.application_id is None:
            return ExecutionOutcome(TaskState.FAILED, "matching task has no application id")
        await self._runner.run(claimed_task.application_id)
        return ExecutionOutcome(
            TaskState.COMPLETED,
            "matching v2 calculation completed",
            (f"application:{claimed_task.application_id}",),
        )


class ResumeRagSyncTaskHandler:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        cv_file_id = claimed_task.payload.get("cv_file_id")
        if not isinstance(cv_file_id, str):
            return ExecutionOutcome(
                TaskState.FAILED,
                "resume RAG synchronization payload is invalid",
                ("failure_code:invalid_task_payload",),
            )

        with self._session_factory() as session:
            cv_file = session.get(CvFileRow, cv_file_id)
            if cv_file is None:
                return ExecutionOutcome(
                    TaskState.FAILED,
                    "resume is unavailable for RAG synchronization",
                    ("failure_code:resume_unavailable",),
                )
            result = await RagSyncService(session, self._settings).sync_resume(cv_file_id)

        if result is None:
            return ExecutionOutcome(
                TaskState.RETRY_SCHEDULED,
                "resume RAG synchronization failed and was scheduled for bounded retry",
                (f"resume:{cv_file_id}", "failure_code:rag_sync_failed"),
            )
        return ExecutionOutcome(
            TaskState.COMPLETED,
            "resume RAG synchronization completed",
            (f"resume:{cv_file_id}", f"rag_status:{result.status}"),
        )


class ApplicationReviewCheckpointHandler:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    async def handle(self, claimed_task: ClaimedTask) -> ExecutionOutcome:
        if claimed_task.application_id is None:
            return ExecutionOutcome(TaskState.FAILED, "review task has no application id")
        with self._session_factory() as session:
            application = session.get(ApplicationRow, claimed_task.application_id)
            if application is None:
                return ExecutionOutcome(TaskState.FAILED, "application no longer exists")
            if application.status == "awaiting_review":
                return ExecutionOutcome(
                    TaskState.WAITING_FOR_USER,
                    "application is available in the human review queue",
                    (f"application:{application.id}",),
                )
            return ExecutionOutcome(
                TaskState.COMPLETED,
                f"application already resolved with status {application.status}",
                (f"application:{application.id}",),
            )


class DurableTaskDispatcher:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        coordinator: RedisCoordinator,
        handlers: Mapping[str, TaskHandler],
        worker_name: str,
        settings: Settings,
        queue_name: str = "dispatcher",
    ) -> None:
        self._session_factory = session_factory
        self._coordinator = coordinator
        self._handlers = handlers
        self._worker_name = worker_name
        self._settings = settings
        self._queue_name = queue_name

    async def run_once(self) -> bool:
        self._heartbeat("polling")
        with self._session_factory() as session:
            claimed_task = SqlTaskRepository(session).claim_next(
                worker=self._worker_name, queue_name=self._queue_name
            )
        if claimed_task is None:
            return False

        lease = await self._coordinator.acquire_lease(
            f"workflow-task:{claimed_task.idempotency_key}",
            lease_seconds=self._settings.worker_lease_seconds,
        )
        if lease is None:
            with self._session_factory() as session:
                SqlTaskRepository(session).reschedule_claim(
                    claimed_task.task_id,
                    worker=self._worker_name,
                    reason="another worker owns the Redis execution lease",
                    delay_seconds=self._settings.worker_retry_seconds,
                )
            return True

        try:
            task_kind = claimed_task.idempotency_key.partition(":")[0]
            handler = self._handlers.get(task_kind)
            if handler is None:
                outcome = ExecutionOutcome(
                    TaskState.FAILED, f"no handler for task kind {task_kind}"
                )
            else:
                outcome = await handler.handle(claimed_task)
            with self._session_factory() as session:
                repository = SqlTaskRepository(session)
                if outcome.state is TaskState.RETRY_SCHEDULED:
                    if claimed_task.attempt_number >= self._settings.worker_max_attempts:
                        repository.finish_claim(
                            claimed_task.task_id,
                            new_state=TaskState.FAILED,
                            reason="task exhausted bounded retry attempts",
                            worker=self._worker_name,
                            evidence=outcome.evidence,
                        )
                    else:
                        repository.retry_claim(
                            claimed_task.task_id,
                            worker=self._worker_name,
                            reason=outcome.reason,
                            delay_seconds=self._settings.worker_retry_seconds,
                            evidence=outcome.evidence,
                        )
                else:
                    evidence = outcome.evidence
                    if outcome.human_action is not None:
                        if outcome.state is not TaskState.WAITING_FOR_USER:
                            raise RuntimeError("Human action requires waiting_for_user outcome")
                        checkpoint = HumanActionCheckpointRow(
                            task_id=claimed_task.task_id,
                            kind=outcome.human_action.kind,
                            instructions=outcome.human_action.instructions,
                            evidence=list(evidence),
                        )
                        session.add(checkpoint)
                        session.flush()
                        evidence = (*evidence, f"checkpoint:{checkpoint.id}")
                        if outcome.human_action.screenshot_path is not None:
                            artifact = EvidenceArtifactStorage(
                                self._settings.artifact_directory,
                                max_artifact_bytes=self._settings.max_evidence_bytes,
                            ).register_screenshot(
                                session,
                                checkpoint_id=checkpoint.id,
                                storage_path=outcome.human_action.screenshot_path,
                                content_type="image/png",
                                commit=False,
                            )
                            evidence = (*evidence, f"artifact:{artifact.id}")
                    repository.finish_claim(
                        claimed_task.task_id,
                        new_state=outcome.state,
                        reason=outcome.reason,
                        worker=self._worker_name,
                        evidence=evidence,
                    )
        except Exception as error:
            with self._session_factory() as session:
                repository = SqlTaskRepository(session)
                if claimed_task.attempt_number >= self._settings.worker_max_attempts:
                    repository.finish_claim(
                        claimed_task.task_id,
                        new_state=TaskState.FAILED,
                        reason="task exhausted bounded retry attempts",
                        worker=self._worker_name,
                        evidence=(type(error).__name__,),
                    )
                else:
                    repository.retry_claim(
                        claimed_task.task_id,
                        worker=self._worker_name,
                        reason="task handler failed and was scheduled for bounded retry",
                        delay_seconds=self._settings.worker_retry_seconds,
                        evidence=(type(error).__name__,),
                    )
        finally:
            await self._coordinator.release_lease(lease)
            self._heartbeat("healthy")
        return True

    def _heartbeat(self, status: str) -> None:
        with self._session_factory() as session:
            session.merge(
                WorkerHeartbeatRow(
                    worker_name=self._worker_name,
                    status=status,
                    last_seen_at=datetime.now(UTC),
                )
            )
            session.commit()


async def run_worker() -> None:
    configure_logging()
    settings = get_settings()
    from app.matching.runtime import MatchingRuntime

    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    coordinator = RedisCoordinator(cast(RedisCoordinationClient, redis_client))
    dispatcher = DurableTaskDispatcher(
        session_factory=SessionFactory,
        coordinator=coordinator,
        handlers={
            "application-review": ApplicationReviewCheckpointHandler(SessionFactory),
            "matching-v2": MatchingTaskHandler(MatchingRuntime(SessionFactory, settings)),
            "rag-resume-sync": ResumeRagSyncTaskHandler(SessionFactory, settings),
        },
        worker_name="dispatcher-1",
        settings=settings,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stopped.set)
    logger = structlog.get_logger()
    logger.info("dispatcher_started", worker="dispatcher-1")
    try:
        while not stopped.is_set():
            processed = await dispatcher.run_once()
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopped.wait(), timeout=settings.worker_poll_seconds)
    finally:
        await redis_client.aclose()
        logger.info("dispatcher_stopped", worker="dispatcher-1")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
