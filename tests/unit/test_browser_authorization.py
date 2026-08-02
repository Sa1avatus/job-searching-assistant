from types import SimpleNamespace

import pytest

from app.services import browser_authorization
from app.services.browser_authorization import (
    BrowserAuthorizationError,
    BrowserAuthorizationManager,
    BrowserAuthorizationSite,
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


@pytest.mark.asyncio
async def test_custom_site_authorization_uses_exact_allowed_host(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(browser_authorization, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = BrowserAuthorizationManager()
    site = BrowserAuthorizationSite(
        site_key="custom-site",
        login_url="https://careers.example.com/login",
        allowed_hosts=("careers.example.com",),
        login_path_markers=("/login",),
    )

    await manager.start(
        user_id="user-1",
        site_key=site.site_key,
        timeout_ms=1_000,
        artifact_directory=tmp_path,
        site=site,
    )
    active = manager._active[("user-1", site.site_key)]
    active.page.url = "https://sub.careers.example.com/jobs"

    with pytest.raises(BrowserAuthorizationError, match="Вход ещё не завершён"):
        await manager.confirm(user_id="user-1", site_key=site.site_key)

    active.page.url = "https://careers.example.com/profile"
    state, last_url = await manager.confirm(user_id="user-1", site_key=site.site_key)

    assert state == {"cookies": [{"name": "session"}], "origins": []}
    assert last_url == "https://careers.example.com/profile"


@pytest.mark.asyncio
async def test_custom_site_requires_explicit_configuration(tmp_path) -> None:
    manager = BrowserAuthorizationManager()

    with pytest.raises(
        BrowserAuthorizationError,
        match="^Site authorization configuration is missing$",
    ):
        await manager.start(
            user_id="user-1",
            site_key="custom-site",
            timeout_ms=1_000,
            artifact_directory=tmp_path,
        )
    assert _FakePlaywrightEngine.latest.is_closed is True
