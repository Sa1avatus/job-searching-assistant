from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import cast

import redis.asyncio as redis
import structlog

from app.config import Settings, get_settings
from app.domain.models import TaskState
from app.matching.jobs import MATCHING_QUEUE_NAME, RETRY_MATCHING_PRIORITY
from app.matching.queue_admin import matching_queue_is_paused
from app.matching.runtime import MatchingRuntime
from app.observability.logging import configure_logging
from app.storage.database import SessionFactory
from app.storage.tables import WorkflowTaskRow
from app.storage.task_repository import SqlTaskRepository
from app.workers.coordination import RedisCoordinationClient, RedisCoordinator
from app.workers.dispatcher import DurableTaskDispatcher, MatchingTaskHandler


def _handle_refresh_requested(task_id: str, new_state: TaskState, worker_prefix: str) -> None:
    """After a matching task completes, auto-reschedule if refresh was requested."""
    if new_state not in (TaskState.COMPLETED, TaskState.FAILED):
        return
    with SessionFactory() as session:
        row = session.get(WorkflowTaskRow, task_id)
        if row is None or not row.refresh_requested:
            return
        # Reset the terminal task back to scheduled so the worker picks it up again
        row.refresh_requested = False
        row.state = TaskState.SCHEDULED.value
        row.scheduled_for = datetime.now(UTC)
        session.commit()
        logger = structlog.get_logger()
        logger.info("matching_refresh_auto_rescheduled", task_id=task_id)


async def _periodic_recovery(
    stopped: asyncio.Event,
    worker_prefix: str,
    settings: Settings,
    interval_seconds: int,
) -> None:
    """Periodically recover stale running tasks from crashed workers."""
    logger = structlog.get_logger()
    while not stopped.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stopped.wait(), timeout=interval_seconds)
        if stopped.is_set():
            break
        try:
            with SessionFactory() as session:
                recovered = SqlTaskRepository(session).recover_stale_running_tasks(
                    cutoff=datetime.now(UTC) - timedelta(seconds=interval_seconds),
                    recovery_worker=f"{worker_prefix}-periodic-recovery",
                    queue_name=MATCHING_QUEUE_NAME,
                    max_attempts=settings.worker_max_attempts,
                )
            if recovered > 0:
                logger.info("periodic_recovery_recovered", count=recovered)
        except Exception:
            logger.warning("periodic_recovery_failed", exc_info=True)


async def _run_slot(
    dispatcher: DurableTaskDispatcher,
    stopped: asyncio.Event,
    redis_client: redis.Redis,
    *,
    poll_seconds: float,
) -> None:
    while not stopped.is_set():
        if await matching_queue_is_paused(redis_client):
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), timeout=poll_seconds)
            continue
        processed = await dispatcher.run_once()
        if not processed:
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), timeout=poll_seconds)


async def run_worker() -> None:
    configure_logging()
    settings = get_settings()
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    coordinator = RedisCoordinator(cast(RedisCoordinationClient, redis_client))
    runner = MatchingRuntime(SessionFactory, settings, redis_client=redis_client)
    handler = MatchingTaskHandler(runner)
    worker_prefix = "matching-worker"

    with SessionFactory() as session:
        recovered = SqlTaskRepository(session).recover_stale_running_tasks(
            cutoff=datetime.now(UTC) - timedelta(seconds=settings.worker_lease_seconds * 2),
            recovery_worker=f"{worker_prefix}-recovery",
            queue_name=MATCHING_QUEUE_NAME,
            max_attempts=settings.worker_max_attempts,
        )

    def _on_complete(task_id: str, new_state: TaskState) -> None:
        _handle_refresh_requested(task_id, new_state, worker_prefix)

    dispatchers = [
        DurableTaskDispatcher(
            session_factory=SessionFactory,
            coordinator=coordinator,
            handlers={"matching-v2": handler},
            worker_name=f"{worker_prefix}-{slot}",
            settings=settings,
            queue_name=MATCHING_QUEUE_NAME,
            retry_priority=RETRY_MATCHING_PRIORITY,
            on_claim_completed=_on_complete,
        )
        for slot in range(1, settings.matching_worker_concurrency + 1)
    ]
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stopped.set)

    logger = structlog.get_logger()
    logger.info(
        "matching_worker_started",
        concurrency=settings.matching_worker_concurrency,
        recovered_tasks=recovered,
    )
    tasks = [
        asyncio.create_task(
            _run_slot(
                dispatcher,
                stopped,
                redis_client,
                poll_seconds=settings.worker_poll_seconds,
            )
        )
        for dispatcher in dispatchers
    ]
    # Periodic stale task recovery — catches orphaned tasks from crashed workers
    recovery_interval = max(settings.worker_lease_seconds, 120)
    tasks.append(
        asyncio.create_task(_periodic_recovery(stopped, worker_prefix, settings, recovery_interval))
    )
    try:
        await stopped.wait()
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await redis_client.aclose()
        logger.info("matching_worker_stopped")


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
