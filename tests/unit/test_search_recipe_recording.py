import json
from types import SimpleNamespace

import pytest

from app.services import search_recipe_recording
from app.services.search_recipe_recording import (
    SearchRecipeRecordingManager,
    SearchRecordingError,
)


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://careers.example.com/"
        self._binding = None

    async def expose_binding(self, _name: str, callback: object) -> None:
        self._binding = callback

    async def add_init_script(self, *, path: str) -> None:
        del path

    async def content(self) -> str:
        return "<html></html>"

    async def emit(self, payload: str) -> None:
        assert self._binding is not None
        await self._binding(None, payload)


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


def _start_kwargs(tmp_path: object, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "user_id": "user-1",
        "site_key": "careers",
        "start_url": "https://careers.example.com/",
        "allowed_hosts": ("careers.example.com",),
        "timeout_ms": 10_000,
        "artifact_directory": tmp_path,
        "storage_state": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_recording_buffers_actions_until_stopped(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(search_recipe_recording, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = SearchRecipeRecordingManager()

    await manager.start(**_start_kwargs(tmp_path))
    assert manager.is_recording(user_id="user-1", site_key="careers") is True

    page = _FakePlaywrightEngine.latest.page
    await page.emit(
        json.dumps({"kind": "fill", "tag": "input", "id": "q", "value": "python", "valueLength": 6})
    )
    await page.emit(json.dumps({"kind": "click", "tag": "button", "id": "go"}))
    page.url = "https://careers.example.com/search?q=python"

    result = await manager.stop(user_id="user-1", site_key="careers")

    assert result.start_url == "https://careers.example.com/"
    assert result.final_url == "https://careers.example.com/search?q=python"
    assert [action.kind for action in result.actions] == ["fill", "click"]
    assert result.actions[0].element_id == "q"
    assert manager.is_recording(user_id="user-1", site_key="careers") is False
    assert _FakePlaywrightEngine.latest.is_closed is True


@pytest.mark.asyncio
async def test_a_second_recording_for_the_same_site_is_refused(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(search_recipe_recording, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = SearchRecipeRecordingManager()
    await manager.start(**_start_kwargs(tmp_path))

    with pytest.raises(SearchRecordingError, match="уже идёт"):
        await manager.start(**_start_kwargs(tmp_path))


@pytest.mark.asyncio
async def test_start_refuses_an_off_host_url(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(search_recipe_recording, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = SearchRecipeRecordingManager()

    with pytest.raises(SearchRecordingError, match="разрешённые хосты"):
        await manager.start(**_start_kwargs(tmp_path, start_url="https://evil.example.org/"))


@pytest.mark.asyncio
async def test_stop_refuses_a_result_that_ended_on_an_off_host_page(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(search_recipe_recording, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = SearchRecipeRecordingManager()
    await manager.start(**_start_kwargs(tmp_path))
    _FakePlaywrightEngine.latest.page.url = "https://evil.example.org/x"

    with pytest.raises(SearchRecordingError, match="неразрешённый хост"):
        await manager.stop(user_id="user-1", site_key="careers")
    assert _FakePlaywrightEngine.latest.is_closed is True


@pytest.mark.asyncio
async def test_stopping_without_a_start_is_refused(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(search_recipe_recording, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = SearchRecipeRecordingManager()

    with pytest.raises(SearchRecordingError, match="не запущена"):
        await manager.stop(user_id="user-1", site_key="careers")


@pytest.mark.asyncio
async def test_cancel_closes_the_browser_without_returning_actions(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(search_recipe_recording, "PlaywrightEngine", _FakePlaywrightEngine)
    manager = SearchRecipeRecordingManager()
    await manager.start(**_start_kwargs(tmp_path))

    await manager.cancel(user_id="user-1", site_key="careers")

    assert manager.is_recording(user_id="user-1", site_key="careers") is False
    assert _FakePlaywrightEngine.latest.is_closed is True
