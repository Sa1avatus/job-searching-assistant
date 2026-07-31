from types import SimpleNamespace

import pytest

from app.services import browser_authorization
from app.services.browser_authorization import (
    BrowserAuthorizationError,
    BrowserAuthorizationManager,
)


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://www.linkedin.com/login"


class _FakePlaywrightEngine:
    latest: "_FakePlaywrightEngine"

    def __init__(self, **_kwargs: object) -> None:
        self.page = _FakePage()
        self.is_closed = False
        _FakePlaywrightEngine.latest = self

    async def __aenter__(self) -> "_FakePlaywrightEngine":
        return self

    async def __aexit__(self, *_error: object) -> None:
        self.is_closed = True

    async def new_page(self) -> _FakePage:
        return self.page

    async def navigate(self, page: _FakePage, url: str) -> SimpleNamespace:
        page.url = url
        return SimpleNamespace(is_successful=True)

    async def storage_state(self) -> dict[str, object]:
        return {"cookies": [{"name": "session"}], "origins": []}


@pytest.mark.asyncio
async def test_authorization_stays_open_until_login_is_complete(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(browser_authorization, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = BrowserAuthorizationManager()

    await manager.start(
        user_id="user-1",
        site_key="linkedin",
        timeout_ms=30_000,
        artifact_directory=tmp_path,
    )

    with pytest.raises(BrowserAuthorizationError, match="Вход ещё не завершён"):
        await manager.confirm(user_id="user-1", site_key="linkedin")
    assert manager.is_waiting(user_id="user-1", site_key="linkedin") is True
    assert _FakePlaywrightEngine.latest.is_closed is False

    _FakePlaywrightEngine.latest.page.url = "https://www.linkedin.com/feed/"
    state, last_url = await manager.confirm(user_id="user-1", site_key="linkedin")

    assert state["cookies"] == [{"name": "session"}]
    assert last_url == "https://www.linkedin.com/feed/"
    assert manager.is_waiting(user_id="user-1", site_key="linkedin") is False
    assert _FakePlaywrightEngine.latest.is_closed is True


@pytest.mark.asyncio
async def test_authorization_can_be_cancelled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(browser_authorization, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = BrowserAuthorizationManager()
    await manager.start(
        user_id="user-1",
        site_key="headhunter",
        timeout_ms=30_000,
        artifact_directory=tmp_path,
    )

    await manager.cancel(user_id="user-1", site_key="headhunter")

    assert manager.is_waiting(user_id="user-1", site_key="headhunter") is False
    assert _FakePlaywrightEngine.latest.is_closed is True
