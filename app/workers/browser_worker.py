from __future__ import annotations

import asyncio
import signal
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import redis.asyncio as redis
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.browser.session_service import BrowserSessionNotFound, BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState
from app.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.storage.database import SessionFactory
from app.storage.tables import BrowserSessionRow, WorkerHeartbeatRow
from app.workers.browser_tasks import (
    ApplicationBrowserTaskHandler,
    ControlledBrowserReviewHandler,
    GreenhouseBrowserReviewHandler,
    HeadHunterApplyHandler,
    LinkedInApplyHandler,
)
from app.workers.coordination import RedisCoordinationClient, RedisCoordinator
from app.workers.dispatcher import DurableTaskDispatcher

WORKER_NAME = "browser-worker-1"


def create_session_store(settings: Settings) -> EncryptedBrowserStateStore:
    if settings.browser_state_encryption_key is None:
        raise RuntimeError("APP_BROWSER_STATE_ENCRYPTION_KEY is required by browser-worker")
    return EncryptedBrowserStateStore(
        settings.artifact_directory,
        encryption_key=settings.browser_state_encryption_key.get_secret_value(),
        max_state_bytes=settings.max_browser_state_bytes,
    )


def audit_browser_sessions(
    session_factory: sessionmaker[Session], store: EncryptedBrowserStateStore
) -> tuple[int, int]:
    restored_count = 0
    corrupted_count = 0
    with session_factory() as session:
        session_ids = session.scalars(
            select(BrowserSessionRow.id).where(
                BrowserSessionRow.status.in_(("available", "active"))
            )
        ).all()
    for browser_session_id in session_ids:
        with session_factory() as session:
            try:
                BrowserSessionService(session, store).restore(browser_session_id)
                restored_count += 1
            except InvalidBrowserState:
                corrupted_count += 1
            except BrowserSessionNotFound:
                continue
    with session_factory() as session:
        session.merge(
            WorkerHeartbeatRow(
                worker_name=WORKER_NAME,
                status="degraded" if corrupted_count else "healthy",
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    return restored_count, corrupted_count


async def run_worker() -> None:
    configure_logging()
    settings = get_settings()
    store = create_session_store(settings)
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    coordinator = RedisCoordinator(cast(RedisCoordinationClient, redis_client))
    dispatcher = DurableTaskDispatcher(
        session_factory=SessionFactory,
        coordinator=coordinator,
        handlers={
            "browser-review": ControlledBrowserReviewHandler(
                settings, Path(__file__).parents[2] / "fixtures"
            ),
            "application-review": ApplicationBrowserTaskHandler(
                GreenhouseBrowserReviewHandler(settings, SessionFactory),
                HeadHunterApplyHandler(settings, SessionFactory, store),
                LinkedInApplyHandler(settings, SessionFactory, store),
            ),
        },
        worker_name=WORKER_NAME,
        settings=settings,
        queue_name="browser",
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stopped.set)
    logger = structlog.get_logger()
    logger.info("browser_worker_started", worker=WORKER_NAME)
    next_session_audit_at = 0.0
    try:
        while not stopped.is_set():
            if time.monotonic() >= next_session_audit_at:
                restored_count, corrupted_count = audit_browser_sessions(SessionFactory, store)
                logger.info(
                    "browser_session_audit_completed",
                    restored_count=restored_count,
                    corrupted_count=corrupted_count,
                )
                next_session_audit_at = time.monotonic() + 30
            processed = await dispatcher.run_once()
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopped.wait(), timeout=settings.worker_poll_seconds)
    finally:
        await redis_client.aclose()
        logger.info("browser_worker_stopped", worker=WORKER_NAME)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
