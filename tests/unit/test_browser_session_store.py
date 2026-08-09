from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState


def create_store(root_directory: Path, encryption_key: bytes) -> EncryptedBrowserStateStore:
    return EncryptedBrowserStateStore(
        root_directory,
        encryption_key=encryption_key.decode("ascii"),
        max_state_bytes=8192,
    )


def test_encrypted_browser_state_round_trips_without_plaintext_leak(tmp_path: Path) -> None:
    store = create_store(tmp_path, Fernet.generate_key())
    state: dict[str, object] = {
        "cookies": [{"name": "session", "value": "private-cookie-value"}],
        "origins": [],
    }

    relative_path = store.save(state)

    assert store.load(relative_path) == state
    encrypted_bytes = (tmp_path / relative_path).read_bytes()
    assert b"private-cookie-value" not in encrypted_bytes


def test_encrypted_browser_state_rejects_wrong_key(tmp_path: Path) -> None:
    relative_path = create_store(tmp_path, Fernet.generate_key()).save(
        {"cookies": [], "origins": []}
    )

    with pytest.raises(InvalidBrowserState, match="failed authentication"):
        create_store(tmp_path, Fernet.generate_key()).load(relative_path)


def test_encrypted_browser_state_rejects_path_escape(tmp_path: Path) -> None:
    store = create_store(tmp_path, Fernet.generate_key())

    with pytest.raises(InvalidBrowserState, match="outside the session root"):
        store.delete("../outside.state.enc")
