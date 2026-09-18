"""Browser session lifecycle as an explicit state machine.

The dashboard must never guess whether a site login is usable; it renders this state.
Pure domain code: transitions are a table, unknown (state, event) pairs are errors.

States
    DISCONNECTED     no session was ever captured for this user/site
    LOGIN_REQUIRED   the site demanded a login (or a login attempt was abandoned)
    AUTHENTICATING   a visible login window is open and waiting for the user
    AUTHENTICATED    a session was captured but not yet verified against the site
    READY            the site confirmed the session is live (last_verified_at is set)
    EXPIRED          verification found the session dead
    REAUTH_REQUIRED  expired/corrupted session the user must replace
"""

from __future__ import annotations

from enum import StrEnum


class BrowserSessionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    READY = "READY"
    EXPIRED = "EXPIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


class BrowserSessionEvent(StrEnum):
    LOGIN_STARTED = "LOGIN_STARTED"
    LOGIN_CONFIRMED = "LOGIN_CONFIRMED"
    LOGIN_CANCELLED = "LOGIN_CANCELLED"
    LOGIN_WALL_HIT = "LOGIN_WALL_HIT"
    VERIFIED_LIVE = "VERIFIED_LIVE"
    VERIFIED_DEAD = "VERIFIED_DEAD"
    EXPIRY_ACKNOWLEDGED = "EXPIRY_ACKNOWLEDGED"
    STATE_CORRUPTED = "STATE_CORRUPTED"
    STATE_DELETED = "STATE_DELETED"


S = BrowserSessionState
E = BrowserSessionEvent

_TRANSITIONS: dict[tuple[S, E], S] = {
    (S.DISCONNECTED, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.DISCONNECTED, E.LOGIN_WALL_HIT): S.LOGIN_REQUIRED,
    (S.LOGIN_REQUIRED, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.LOGIN_REQUIRED, E.LOGIN_WALL_HIT): S.LOGIN_REQUIRED,
    (S.LOGIN_REQUIRED, E.STATE_DELETED): S.DISCONNECTED,
    (S.AUTHENTICATING, E.LOGIN_CONFIRMED): S.AUTHENTICATED,
    # The worker saved a verified capture even though a concurrent status read had already
    # flipped a vanished window to LOGIN_REQUIRED: the capture is the truth.
    (S.LOGIN_REQUIRED, E.LOGIN_CONFIRMED): S.AUTHENTICATED,
    (S.AUTHENTICATING, E.LOGIN_CANCELLED): S.LOGIN_REQUIRED,
    (S.AUTHENTICATING, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.AUTHENTICATED, E.VERIFIED_LIVE): S.READY,
    (S.AUTHENTICATED, E.VERIFIED_DEAD): S.EXPIRED,
    (S.AUTHENTICATED, E.LOGIN_WALL_HIT): S.EXPIRED,
    (S.AUTHENTICATED, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.AUTHENTICATED, E.STATE_CORRUPTED): S.REAUTH_REQUIRED,
    (S.AUTHENTICATED, E.STATE_DELETED): S.DISCONNECTED,
    (S.READY, E.VERIFIED_LIVE): S.READY,
    (S.READY, E.VERIFIED_DEAD): S.EXPIRED,
    (S.READY, E.LOGIN_WALL_HIT): S.EXPIRED,
    (S.READY, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.READY, E.STATE_CORRUPTED): S.REAUTH_REQUIRED,
    (S.READY, E.STATE_DELETED): S.DISCONNECTED,
    (S.EXPIRED, E.EXPIRY_ACKNOWLEDGED): S.REAUTH_REQUIRED,
    (S.EXPIRED, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.EXPIRED, E.STATE_DELETED): S.DISCONNECTED,
    (S.REAUTH_REQUIRED, E.LOGIN_STARTED): S.AUTHENTICATING,
    (S.REAUTH_REQUIRED, E.LOGIN_WALL_HIT): S.REAUTH_REQUIRED,
    (S.REAUTH_REQUIRED, E.STATE_DELETED): S.DISCONNECTED,
}

# A session credential exists in these states (used by the compatibility flags).
USABLE_STATES = frozenset({S.AUTHENTICATED, S.READY})

RECOVERY_HINTS: dict[S, str] = {
    S.DISCONNECTED: "Start a login to connect this site.",
    S.LOGIN_REQUIRED: "The site needs a login. Start a login and sign in manually.",
    S.AUTHENTICATING: "Finish signing in in the open window, then confirm.",
    S.AUTHENTICATED: "Session saved; it will be verified against the site.",
    S.READY: "",
    S.EXPIRED: "The session stopped working. Start a login again.",
    S.REAUTH_REQUIRED: "The saved session is no longer usable. Start a login again.",
}


class IllegalSessionTransition(ValueError):
    """Raised when an event is not allowed in the current state (maps to HTTP 409)."""

    def __init__(self, state: BrowserSessionState, event: BrowserSessionEvent) -> None:
        super().__init__(f"Event {event} is not allowed while the session is {state}")
        self.state = state
        self.event = event


def next_state(state: BrowserSessionState, event: BrowserSessionEvent) -> BrowserSessionState:
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError:
        raise IllegalSessionTransition(state, event) from None


def can_apply(state: BrowserSessionState, event: BrowserSessionEvent) -> bool:
    return (state, event) in _TRANSITIONS
