import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.storage.database import Base
from app.storage.documents import DocumentStorage
from app.storage.retention import (
    purge_expired_artifacts,
    purge_expired_browser_sessions,
    purge_expired_cv_files,
)
from app.storage.tables import BrowserSessionRow, CvFileRow, UserRow


def test_retention_deletes_only_expired_files_inside_artifact_root(tmp_path: Path) -> None:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()
    old_artifact = artifact_directory / "old.txt"
    current_artifact = artifact_directory / "current.txt"
    outside_file = tmp_path / "outside.txt"
    for path in (old_artifact, current_artifact, outside_file):
        path.write_text("content", encoding="utf-8")
    now = datetime.now(UTC)
    old_timestamp = (now - timedelta(days=31)).timestamp()
    os.utime(old_artifact, (old_timestamp, old_timestamp))

    report = purge_expired_artifacts(artifact_directory, retention_days=30, now=now)

    assert report.deleted_files == 1
    assert report.retained_files == 1
    assert old_artifact.exists() is False
    assert current_artifact.exists() is True
    assert outside_file.exists() is True


def test_cv_retention_deletes_database_record_and_physical_file(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    storage = DocumentStorage(tmp_path / "documents", max_document_bytes=1024)
    document = storage.save("resume.pdf", "application/pdf", b"%PDF-1.7\n%%EOF")
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        cv_file = CvFileRow(
            user_id=user.id,
            original_filename=document.original_filename,
            storage_path=str(document.storage_path),
            content_type=document.content_type,
            sha256=document.sha256,
            size_bytes=document.size_bytes,
            created_at=now - timedelta(days=31),
        )
        session.add(cv_file)
        session.commit()

        report = purge_expired_cv_files(session, storage, retention_days=30, now=now)

        assert report.deleted_records == 1
        assert session.get(CvFileRow, cv_file.id) is None
        assert document.storage_path.exists() is False


def test_browser_session_retention_deletes_record_and_encrypted_file(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    artifact_directory = tmp_path / "artifacts"
    browser_directory = artifact_directory / "browser-sessions"
    browser_directory.mkdir(parents=True)
    state_path = browser_directory / "state.enc"
    state_path.write_bytes(b"encrypted")
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.flush()
        browser_session = BrowserSessionRow(
            user_id=user.id,
            site_key="controlled.test",
            adapter_name="controlled",
            encrypted_state_path="browser-sessions/state.enc",
            updated_at=now - timedelta(days=31),
        )
        session.add(browser_session)
        session.commit()

        report = purge_expired_browser_sessions(
            session, artifact_directory, retention_days=30, now=now
        )

        assert report.deleted_records == 1
        assert session.get(BrowserSessionRow, browser_session.id) is None
        assert state_path.exists() is False
