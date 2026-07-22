from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class InvalidBrowserState(ValueError):
    pass


def delete_browser_state_file(root_directory: Path, relative_path: str) -> None:
    session_root = (root_directory / "browser-sessions").resolve()
    candidate = (root_directory / relative_path).resolve()
    if not candidate.is_relative_to(session_root):
        raise InvalidBrowserState("Browser state path is outside the session root")
    candidate.unlink(missing_ok=True)


class EncryptedBrowserStateStore:
    def __init__(
        self,
        root_directory: Path,
        *,
        encryption_key: str,
        max_state_bytes: int,
    ) -> None:
        self._root_directory = (root_directory / "browser-sessions").resolve()
        self._max_state_bytes = max_state_bytes
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise InvalidBrowserState("Browser state encryption key is invalid") from error

    def save(self, state: dict[str, object]) -> str:
        serialized = json.dumps(state, separators=(",", ":")).encode("utf-8")
        if len(serialized) > self._max_state_bytes:
            raise InvalidBrowserState("Browser state exceeds the configured size limit")
        encrypted = self._cipher.encrypt(serialized)
        self._root_directory.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4()}.state.enc"
        destination = self._root_directory / filename
        temporary = destination.with_suffix(".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return f"browser-sessions/{filename}"

    def load(self, relative_path: str) -> dict[str, object]:
        path = self._resolve(relative_path)
        if not path.is_file():
            raise InvalidBrowserState("Encrypted browser state is unavailable")
        try:
            if path.stat().st_size > self._max_state_bytes * 2:
                raise InvalidBrowserState(
                    "Encrypted browser state exceeds the configured size limit"
                )
            encrypted = path.read_bytes()
        except OSError as error:
            raise InvalidBrowserState("Encrypted browser state is unavailable") from error
        try:
            serialized = self._cipher.decrypt(encrypted)
        except InvalidToken as error:
            raise InvalidBrowserState("Encrypted browser state failed authentication") from error
        if len(serialized) > self._max_state_bytes:
            raise InvalidBrowserState("Browser state exceeds the configured size limit")
        try:
            state = json.loads(serialized)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise InvalidBrowserState("Browser state JSON is invalid") from error
        if not isinstance(state, dict):
            raise InvalidBrowserState("Browser state must be a JSON object")
        if not isinstance(state.get("cookies", []), list) or not isinstance(
            state.get("origins", []), list
        ):
            raise InvalidBrowserState("Browser state has an invalid Playwright structure")
        return state

    def delete(self, relative_path: str) -> None:
        delete_browser_state_file(self._root_directory.parent, relative_path)

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self._root_directory.parent / relative_path).resolve()
        if not candidate.is_relative_to(self._root_directory):
            raise InvalidBrowserState("Browser state path is outside the session root")
        return candidate
