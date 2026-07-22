from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.browser.session_service import BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState
from app.storage.database import Base
from app.storage.tables import BrowserSessionRow, UserRow


def create_store(root_directory: Path, key: bytes) -> EncryptedBrowserStateStore:
    return EncryptedBrowserStateStore(
        root_directory,
        encryption_key=key.decode("ascii"),
        max_state_bytes=4096,
    )


def test_browser_session_service_replaces_and_restores_encrypted_state(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = create_store(tmp_path, Fernet.generate_key())
    with Session(engine) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.commit()
        service = BrowserSessionService(session, store)
        original = service.save(
            user_id=user.id,
            site_key="controlled.test",
            adapter_name="controlled",
            state={"cookies": [], "origins": []},
            last_url="https://controlled.test/start",
        )
        original_path = original.encrypted_state_path
        replacement = service.save(
            user_id=user.id,
            site_key="controlled.test",
            adapter_name="controlled",
            state={"cookies": [{"name": "session", "value": "restored"}], "origins": []},
            last_url="https://controlled.test/review",
        )
        persisted_at = replacement.updated_at

        row, state = service.restore(replacement.id)

        assert row.id == original.id
        assert row.last_restored_at is not None
        assert row.updated_at == persisted_at
        assert state["cookies"] == [{"name": "session", "value": "restored"}]
        assert (tmp_path / original_path).exists() is False
        assert (tmp_path / replacement.encrypted_state_path).is_file()


def test_browser_session_service_marks_state_corrupted_on_authentication_failure(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.commit()
        saved = BrowserSessionService(session, create_store(tmp_path, Fernet.generate_key())).save(
            user_id=user.id,
            site_key="controlled.test",
            adapter_name="controlled",
            state={"cookies": [], "origins": []},
        )

        with pytest.raises(InvalidBrowserState, match="failed authentication"):
            BrowserSessionService(session, create_store(tmp_path, Fernet.generate_key())).restore(
                saved.id
            )

        session.refresh(saved)
        assert saved.status == "corrupted"
        assert session.get(BrowserSessionRow, saved.id) is not None
