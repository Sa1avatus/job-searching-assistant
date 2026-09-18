"""Persist and validate the lifecycle state of a user's login on a site."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.browser_session_state import (
    RECOVERY_HINTS,
    USABLE_STATES,
    BrowserSessionEvent,
    BrowserSessionState,
    can_apply,
    next_state,
)
from app.storage.tables import BrowserSessionRow, BrowserSessionStateRow

# A window that was opened moments ago may not be reported by the worker yet; never treat a
# very recent AUTHENTICATING state as stale.
STALE_WINDOW_GRACE = timedelta(seconds=15)


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: BrowserSessionState
    last_verified_at: datetime | None
    last_error: str | None
    updated_at: datetime | None

    @property
    def is_authorized(self) -> bool:
        return self.state in USABLE_STATES

    @property
    def is_waiting_for_login(self) -> bool:
        return self.state is BrowserSessionState.AUTHENTICATING

    @property
    def recovery_hint(self) -> str:
        return RECOVERY_HINTS[self.state]


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class BrowserSessionStateService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def current(self, user_id: str, site_key: str) -> SessionSnapshot:
        row = self._state_row(user_id, site_key)
        if row is not None:
            return SessionSnapshot(
                BrowserSessionState(row.state),
                _aware(row.last_verified_at),
                row.last_error,
                _aware(row.updated_at),
            )
        return SessionSnapshot(self._legacy_state(user_id, site_key), None, None, None)

    def can_apply(self, user_id: str, site_key: str, event: BrowserSessionEvent) -> bool:
        return can_apply(self.current(user_id, site_key).state, event)

    def apply(
        self,
        user_id: str,
        site_key: str,
        event: BrowserSessionEvent,
        *,
        error: str | None = None,
    ) -> SessionSnapshot:
        """Apply ``event``; raises IllegalSessionTransition when it is not allowed."""
        snapshot = self.current(user_id, site_key)
        target = next_state(snapshot.state, event)
        now = datetime.now(UTC)
        row = self._state_row(user_id, site_key)
        if row is None:
            row = BrowserSessionStateRow(user_id=user_id, site_key=site_key)
            self._session.add(row)
        row.state = target.value
        row.updated_at = now
        if event is BrowserSessionEvent.VERIFIED_LIVE:
            row.last_verified_at = now
            row.last_error = None
        elif event in {
            BrowserSessionEvent.LOGIN_CONFIRMED,
            BrowserSessionEvent.LOGIN_STARTED,
        }:
            row.last_error = None
        elif error is not None:
            row.last_error = error[:1000]
        self._session.commit()
        return self.current(user_id, site_key)

    def reconcile_waiting(
        self, user_id: str, site_key: str, *, worker_is_waiting: bool | None
    ) -> SessionSnapshot:
        """Drop an AUTHENTICATING state whose login window no longer exists.

        ``worker_is_waiting`` is None when the worker could not be asked; the state is then
        left untouched (an unreachable worker proves nothing about the window).
        """
        snapshot = self.current(user_id, site_key)
        if (
            snapshot.state is not BrowserSessionState.AUTHENTICATING
            or worker_is_waiting is not False
        ):
            return snapshot
        if snapshot.updated_at and datetime.now(UTC) - snapshot.updated_at < STALE_WINDOW_GRACE:
            return snapshot
        return self.cancel_login(user_id, site_key, error="Login window is no longer open")

    def cancel_login(
        self, user_id: str, site_key: str, *, error: str | None = None
    ) -> SessionSnapshot:
        """Leave AUTHENTICATING without a new capture.

        A user who had a working session and abandoned a re-login keeps it (their stored
        credential is untouched); otherwise the site is back to needing a login.
        """
        if self._has_usable_stored_session(user_id, site_key):
            return self.apply(user_id, site_key, BrowserSessionEvent.LOGIN_CONFIRMED)
        return self.apply(user_id, site_key, BrowserSessionEvent.LOGIN_CANCELLED, error=error)

    def record_verification(
        self, user_id: str, site_key: str, *, is_live: bool, error: str | None = None
    ) -> SessionSnapshot:
        """Apply a probe result: live -> READY, dead -> EXPIRED -> REAUTH_REQUIRED."""
        if is_live:
            return self.apply(user_id, site_key, BrowserSessionEvent.VERIFIED_LIVE)
        self.apply(
            user_id,
            site_key,
            BrowserSessionEvent.VERIFIED_DEAD,
            error=error or "The site no longer accepts the saved session",
        )
        return self.apply(user_id, site_key, BrowserSessionEvent.EXPIRY_ACKNOWLEDGED)

    def _state_row(self, user_id: str, site_key: str) -> BrowserSessionStateRow | None:
        return self._session.get(BrowserSessionStateRow, (user_id, site_key))

    def _stored_session(self, user_id: str, site_key: str) -> BrowserSessionRow | None:
        return self._session.scalar(
            select(BrowserSessionRow).where(
                BrowserSessionRow.user_id == user_id, BrowserSessionRow.site_key == site_key
            )
        )

    def _has_usable_stored_session(self, user_id: str, site_key: str) -> bool:
        stored = self._stored_session(user_id, site_key)
        return stored is not None and stored.status in {"available", "active"}

    def _legacy_state(self, user_id: str, site_key: str) -> BrowserSessionState:
        """State of a session captured before lifecycle tracking existed."""
        stored = self._stored_session(user_id, site_key)
        if stored is None:
            return BrowserSessionState.DISCONNECTED
        if stored.status in {"available", "active"}:
            return BrowserSessionState.AUTHENTICATED
        return BrowserSessionState.REAUTH_REQUIRED
