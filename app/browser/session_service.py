from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.browser.session_store import EncryptedBrowserStateStore, InvalidBrowserState
from app.storage.tables import BrowserSessionRow, UserRow


class BrowserSessionNotFound(LookupError):
    pass


class BrowserSessionService:
    def __init__(self, session: Session, store: EncryptedBrowserStateStore) -> None:
        self._session = session
        self._store = store

    def save(
        self,
        *,
        user_id: str,
        site_key: str,
        adapter_name: str,
        state: dict[str, object],
        last_url: str | None = None,
    ) -> BrowserSessionRow:
        if self._session.get(UserRow, user_id) is None:
            raise BrowserSessionNotFound("User not found")
        existing = self._session.scalar(
            select(BrowserSessionRow).where(
                BrowserSessionRow.user_id == user_id,
                BrowserSessionRow.site_key == site_key,
            )
        )
        new_path = self._store.save(state)
        previous_path = existing.encrypted_state_path if existing else None
        row = existing or BrowserSessionRow(
            user_id=user_id,
            site_key=site_key,
            adapter_name=adapter_name,
            encrypted_state_path=new_path,
        )
        row.adapter_name = adapter_name
        row.encrypted_state_path = new_path
        row.status = "available"
        row.last_url = last_url
        row.updated_at = datetime.now(UTC)
        self._session.add(row)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            self._store.delete(new_path)
            raise
        if previous_path and previous_path != new_path:
            self._store.delete(previous_path)
        return row

    def restore(self, browser_session_id: str) -> tuple[BrowserSessionRow, dict[str, object]]:
        row = self._session.get(BrowserSessionRow, browser_session_id)
        if row is None:
            raise BrowserSessionNotFound("Browser session not found")
        try:
            state = self._store.load(row.encrypted_state_path)
        except InvalidBrowserState:
            row.status = "corrupted"
            row.updated_at = datetime.now(UTC)
            self._session.commit()
            raise
        row.status = "available"
        row.last_restored_at = datetime.now(UTC)
        self._session.commit()
        return row, state

    def delete(self, browser_session_id: str) -> None:
        row = self._session.get(BrowserSessionRow, browser_session_id)
        if row is None:
            raise BrowserSessionNotFound("Browser session not found")
        relative_path = row.encrypted_state_path
        self._session.delete(row)
        self._session.commit()
        self._store.delete(relative_path)
