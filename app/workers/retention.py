from __future__ import annotations

import signal
import threading
from datetime import UTC, datetime

import structlog

from app.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.storage.database import SessionFactory
from app.storage.documents import DocumentStorage
from app.storage.retention import (
    purge_expired_artifacts,
    purge_expired_browser_sessions,
    purge_expired_cv_files,
)
from app.storage.tables import WorkerHeartbeatRow


def run_retention_cycle(settings: Settings) -> None:
    document_storage = DocumentStorage(
        settings.artifact_directory / "documents",
        max_document_bytes=settings.max_document_bytes,
    )
    with SessionFactory() as session:
        cv_report = purge_expired_cv_files(
            session,
            document_storage,
            retention_days=settings.retention_days,
        )
        browser_session_report = purge_expired_browser_sessions(
            session,
            settings.artifact_directory,
            retention_days=settings.retention_days,
        )
        session.merge(
            WorkerHeartbeatRow(
                worker_name="retention",
                status="healthy",
                last_seen_at=datetime.now(UTC),
            )
        )
        session.commit()
    artifact_report = purge_expired_artifacts(
        settings.artifact_directory,
        retention_days=settings.retention_days,
        excluded_directories=frozenset({"browser-sessions", "documents", "selectors"}),
    )
    structlog.get_logger().info(
        "retention_cycle_completed",
        deleted_cv_records=cv_report.deleted_records,
        failed_cv_records=cv_report.failed_records,
        deleted_browser_sessions=browser_session_report.deleted_records,
        failed_browser_sessions=browser_session_report.failed_records,
        deleted_artifacts=artifact_report.deleted_files,
        reclaimed_bytes=artifact_report.reclaimed_bytes,
    )


def main() -> None:
    configure_logging()
    settings = get_settings()
    stopped = threading.Event()

    def request_shutdown(_signal_number: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    while not stopped.is_set():
        run_retention_cycle(settings)
        stopped.wait(settings.retention_interval_seconds)


if __name__ == "__main__":
    main()
