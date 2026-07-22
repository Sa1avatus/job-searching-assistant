from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.browser.session_store import InvalidBrowserState, delete_browser_state_file
from app.storage.documents import DocumentStorage, InvalidDocumentError
from app.storage.tables import BrowserSessionRow, CvFileRow


@dataclass(frozen=True, slots=True)
class RetentionReport:
    deleted_files: int
    retained_files: int
    reclaimed_bytes: int


@dataclass(frozen=True, slots=True)
class CvRetentionReport:
    deleted_records: int
    failed_records: int


@dataclass(frozen=True, slots=True)
class BrowserSessionRetentionReport:
    deleted_records: int
    failed_records: int


def purge_expired_artifacts(
    artifact_directory: Path,
    *,
    retention_days: int,
    now: datetime | None = None,
    excluded_directories: frozenset[str] = frozenset(),
) -> RetentionReport:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    root = artifact_directory.resolve()
    if not root.exists():
        return RetentionReport(0, 0, 0)
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    deleted_files = 0
    retained_files = 0
    reclaimed_bytes = 0
    for artifact_path in root.rglob("*"):
        if not artifact_path.is_file():
            continue
        relative_path = artifact_path.relative_to(root)
        if relative_path.parts and relative_path.parts[0] in excluded_directories:
            continue
        resolved_path = artifact_path.resolve()
        if not resolved_path.is_relative_to(root):
            continue
        modified_at = datetime.fromtimestamp(resolved_path.stat().st_mtime, tz=UTC)
        if modified_at >= cutoff:
            retained_files += 1
            continue
        reclaimed_bytes += resolved_path.stat().st_size
        resolved_path.unlink()
        deleted_files += 1
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    return RetentionReport(deleted_files, retained_files, reclaimed_bytes)


def purge_expired_cv_files(
    session: Session,
    storage: DocumentStorage,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> CvRetentionReport:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    expired_files = session.scalars(
        select(CvFileRow).where(CvFileRow.created_at < cutoff).order_by(CvFileRow.created_at)
    ).all()
    deleted_records = 0
    failed_records = 0
    for cv_file in expired_files:
        try:
            storage.delete(Path(cv_file.storage_path))
        except (InvalidDocumentError, OSError):
            failed_records += 1
            continue
        session.delete(cv_file)
        deleted_records += 1
    session.commit()
    return CvRetentionReport(deleted_records, failed_records)


def purge_expired_browser_sessions(
    session: Session,
    artifact_directory: Path,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> BrowserSessionRetentionReport:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    expired_sessions = session.scalars(
        select(BrowserSessionRow)
        .where(BrowserSessionRow.updated_at < cutoff)
        .order_by(BrowserSessionRow.updated_at)
    ).all()
    deleted_records = 0
    failed_records = 0
    for browser_session in expired_sessions:
        try:
            delete_browser_state_file(artifact_directory, browser_session.encrypted_state_path)
        except (InvalidBrowserState, OSError):
            failed_records += 1
            continue
        session.delete(browser_session)
        deleted_records += 1
    session.commit()
    return BrowserSessionRetentionReport(deleted_records, failed_records)
