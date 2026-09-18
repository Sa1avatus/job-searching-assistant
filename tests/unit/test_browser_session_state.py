from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.browser_session_state import (
    BrowserSessionEvent as E,
)
from app.domain.browser_session_state import (
    BrowserSessionState as S,
)
from app.domain.browser_session_state import (
    IllegalSessionTransition,
    can_apply,
    next_state,
)
from app.services.browser_session_state import STALE_WINDOW_GRACE, BrowserSessionStateService
from app.storage.database import Base
from app.storage.tables import BrowserSessionRow, BrowserSessionStateRow, UserRow


def test_happy_path_and_expiry_path() -> None:
    state = S.DISCONNECTED
    for event, expected in (
        (E.LOGIN_STARTED, S.AUTHENTICATING),
        (E.LOGIN_CONFIRMED, S.AUTHENTICATED),
        (E.VERIFIED_LIVE, S.READY),
        (E.VERIFIED_DEAD, S.EXPIRED),
        (E.EXPIRY_ACKNOWLEDGED, S.REAUTH_REQUIRED),
        (E.LOGIN_STARTED, S.AUTHENTICATING),
    ):
        state = next_state(state, event)
        assert state is expected


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (S.DISCONNECTED, E.LOGIN_CONFIRMED),  # nothing to confirm
        (S.DISCONNECTED, E.VERIFIED_LIVE),  # cannot verify what does not exist
        (S.AUTHENTICATING, E.VERIFIED_LIVE),  # login not finished
        (S.READY, E.EXPIRY_ACKNOWLEDGED),
        (S.REAUTH_REQUIRED, E.VERIFIED_LIVE),
    ],
)
def test_illegal_transitions_are_rejected(state: S, event: E) -> None:
    assert not can_apply(state, event)
    with pytest.raises(IllegalSessionTransition):
        next_state(state, event)


def test_every_state_can_start_a_login() -> None:
    assert all(can_apply(state, E.LOGIN_STARTED) for state in S)


def _service():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(UserRow(id="u1", display_name="U"))
    session.commit()
    return session, BrowserSessionStateService(session)


def _stored_session(session, status: str = "available") -> None:
    session.add(
        BrowserSessionRow(
            user_id="u1",
            site_key="headhunter",
            adapter_name="headhunter",
            encrypted_state_path=f"p-{status}",
            status=status,
        )
    )
    session.commit()


def test_new_site_is_disconnected() -> None:
    _, service = _service()

    assert service.current("u1", "headhunter").state is S.DISCONNECTED


def test_legacy_sessions_bootstrap_from_the_credential_row() -> None:
    session, service = _service()
    _stored_session(session, "available")
    assert service.current("u1", "headhunter").state is S.AUTHENTICATED

    session.query(BrowserSessionRow).update({"status": "expired"})
    session.commit()
    assert service.current("u1", "headhunter").state is S.REAUTH_REQUIRED


def test_apply_persists_state_and_verification_time() -> None:
    session, service = _service()
    service.apply("u1", "headhunter", E.LOGIN_STARTED)
    service.apply("u1", "headhunter", E.LOGIN_CONFIRMED)
    snapshot = service.record_verification("u1", "headhunter", is_live=True)

    assert snapshot.state is S.READY and snapshot.last_verified_at is not None
    assert session.get(BrowserSessionStateRow, ("u1", "headhunter")).state == "READY"
    with pytest.raises(IllegalSessionTransition):
        service.apply("u1", "headhunter", E.EXPIRY_ACKNOWLEDGED)


def test_dead_verification_ends_in_reauth_required_with_reason() -> None:
    _, service = _service()
    service.apply("u1", "headhunter", E.LOGIN_STARTED)
    service.apply("u1", "headhunter", E.LOGIN_CONFIRMED)

    snapshot = service.record_verification("u1", "headhunter", is_live=False)

    assert snapshot.state is S.REAUTH_REQUIRED
    assert snapshot.last_error


def test_stale_login_window_is_dropped_only_after_the_grace_period() -> None:
    session, service = _service()
    service.apply("u1", "headhunter", E.LOGIN_STARTED)

    fresh = service.reconcile_waiting("u1", "headhunter", worker_is_waiting=False)
    assert fresh.state is S.AUTHENTICATING  # window may not be reported yet

    session.get(BrowserSessionStateRow, ("u1", "headhunter")).updated_at = (
        datetime.now(UTC) - STALE_WINDOW_GRACE - timedelta(seconds=1)
    )
    session.commit()
    assert (
        service.reconcile_waiting("u1", "headhunter", worker_is_waiting=None).state
        is S.AUTHENTICATING
    )  # an unreachable worker proves nothing
    assert (
        service.reconcile_waiting("u1", "headhunter", worker_is_waiting=True).state
        is S.AUTHENTICATING
    )
    dropped = service.reconcile_waiting("u1", "headhunter", worker_is_waiting=False)
    assert dropped.state is S.LOGIN_REQUIRED


def test_abandoning_a_relogin_keeps_the_previous_capture() -> None:
    session, service = _service()
    _stored_session(session)
    service.apply("u1", "headhunter", E.LOGIN_STARTED)

    assert service.cancel_login("u1", "headhunter").state is S.AUTHENTICATED
