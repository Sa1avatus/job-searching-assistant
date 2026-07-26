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

from app.browser.session_probe import probe_browser_session
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


async def probe_active_browser_sessions(
    session_factory: sessionmaker[Session],
    store: EncryptedBrowserStateStore,
    settings: Settings,
) -> tuple[int, int, int]:
    """Check live authentication for all usable sessions without performing site actions."""
    with session_factory() as session:
        session_rows = session.scalars(
            select(BrowserSessionRow).where(
                BrowserSessionRow.status.in_(("available", "active"))
            )
        ).all()

    live_count = 0
    expired_count = 0
    unknown_count = 0
    for browser_session in session_rows:
        with session_factory() as session:
            try:
                _row, state = BrowserSessionService(session, store).restore(browser_session.id)
            except (BrowserSessionNotFound, InvalidBrowserState):
                unknown_count += 1
                continue
        result = await probe_browser_session(
            site_key=browser_session.site_key,
            state=state,
            headless=settings.browser_headless,
            timeout_ms=settings.browser_timeout_ms,
            artifact_directory=settings.artifact_directory,
        )
        if result.is_live is True:
            live_count += 1
        elif result.is_live is False:
            expired_count += 1
            with session_factory() as session:
                stored_row = session.get(BrowserSessionRow, browser_session.id)
                if stored_row is not None:
                    stored_row.status = "expired"
                    session.commit()
        else:
            unknown_count += 1
    return live_count, expired_count, unknown_count


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
    next_live_session_probe_at = 0.0
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
            if time.monotonic() >= next_live_session_probe_at:
                live_count, expired_count, unknown_count = await probe_active_browser_sessions(
                    SessionFactory, store, settings
                )
                logger.info(
                    "browser_session_live_probe_completed",
                    live_count=live_count,
                    expired_count=expired_count,
                    unknown_count=unknown_count,
                )
                next_live_session_probe_at = time.monotonic() + 300
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
