"""Each saved session is probed on its own schedule (custom sites far more often than
hh.ru/LinkedIn by default, either overridable per site) - see app.workers.browser_worker.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.browser.session_probe import BrowserSessionProbe
from app.services.browser_session_probe_settings import set_interval_seconds
from app.storage.database import Base
from app.storage.tables import BrowserSessionRow, UserRow
from app.workers.browser_worker import probe_active_browser_sessions

FAKE_SETTINGS = SimpleNamespace(
    browser_headless=True, browser_timeout_ms=1000, artifact_directory="/tmp"
)


@pytest.fixture
def factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine, expire_on_commit=False)
    with made() as db:
        db.add(UserRow(id="u1", display_name="U"))
        db.add(
            BrowserSessionRow(
                user_id="u1",
                site_key="careerviet",
                adapter_name="custom",
                encrypted_state_path="state-file",
                status="available",
            )
        )
        db.commit()
    return made


@pytest.fixture
def store() -> SimpleNamespace:
    return SimpleNamespace(load=lambda _path: {"cookies": []})


def test_a_never_probed_session_is_checked_immediately(
    factory: sessionmaker, store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable_probe(**_kwargs: object) -> BrowserSessionProbe:
        return BrowserSessionProbe(None, datetime.now(UTC))

    monkeypatch.setattr("app.workers.browser_worker.probe_browser_session", unreachable_probe)

    # careerviet has no SiteDefinitionRow here, so the probe is skipped as "unknown" once
    # attempted - the point of this test is that it *was* attempted, not deferred by scheduling.
    live, expired, unknown = asyncio.run(
        probe_active_browser_sessions(factory, store, FAKE_SETTINGS, {})  # type: ignore[arg-type]
    )

    assert (live, expired, unknown) == (0, 0, 1)


def test_a_recently_probed_session_is_not_probed_again_before_its_interval(
    factory: sessionmaker, store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe_calls = 0

    async def counting_probe(**_kwargs: object) -> BrowserSessionProbe:
        nonlocal probe_calls
        probe_calls += 1
        return BrowserSessionProbe(True, datetime.now(UTC))

    monkeypatch.setattr("app.workers.browser_worker.probe_browser_session", counting_probe)
    with factory() as db:
        set_interval_seconds(db, "u1", "careerviet", 3600)

    next_due: dict[tuple[str, str], float] = {("u1", "careerviet"): time.monotonic() + 3600}
    asyncio.run(probe_active_browser_sessions(factory, store, FAKE_SETTINGS, next_due))  # type: ignore[arg-type]

    assert probe_calls == 0


def test_interval_override_is_honoured_when_scheduling_the_next_probe(
    factory: sessionmaker, store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable_probe(**_kwargs: object) -> BrowserSessionProbe:
        return BrowserSessionProbe(None, datetime.now(UTC))

    monkeypatch.setattr("app.workers.browser_worker.probe_browser_session", unreachable_probe)
    with factory() as db:
        set_interval_seconds(db, "u1", "careerviet", 90)

    next_due: dict[tuple[str, str], float] = {}
    before = time.monotonic()
    asyncio.run(probe_active_browser_sessions(factory, store, FAKE_SETTINGS, next_due))  # type: ignore[arg-type]

    scheduled = next_due[("u1", "careerviet")]
    assert 89 <= scheduled - before <= 91
