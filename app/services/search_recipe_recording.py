"""Records a person manually searching on a user-defined site.

A fixed, observational script (``app/browser/assets/search_recorder.js``) is injected into a
visible Playwright session; it never acts on the page itself, only reports which control was
clicked and which fields were edited (ADR 0003: no arbitrary JavaScript execution, no direct
control of browser actions). Those reports become candidate ``navigate``/``fill``/``click``
reach-steps once the person tags which field was the query and which was the location.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Page

from app.browser.engine import PlaywrightEngine
from app.browser.search_reach_recording import RecordedAction
from app.domain.search_recipe import is_allowed_host

_RECORDER_SCRIPT = Path(__file__).resolve().parents[1] / "browser" / "assets" / "search_recorder.js"
_MAX_RECORDED_ACTIONS = 200


class SearchRecordingError(RuntimeError):
    pass


@dataclass(slots=True)
class _ActiveRecording:
    engine: PlaywrightEngine
    page: Page
    allowed_hosts: tuple[str, ...]
    start_url: str
    actions: list[RecordedAction] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RecordingResult:
    start_url: str
    final_url: str
    final_html: str
    actions: tuple[RecordedAction, ...]


class SearchRecipeRecordingManager:
    """Owns visible, short-lived recording browsers started explicitly from the local dashboard."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], _ActiveRecording] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        user_id: str,
        site_key: str,
        start_url: str,
        allowed_hosts: tuple[str, ...],
        timeout_ms: int,
        artifact_directory: Path,
        storage_state: dict[str, object] | None,
    ) -> None:
        if urlsplit(start_url).scheme != "https" or not is_allowed_host(
            urlsplit(start_url).hostname, allowed_hosts
        ):
            raise SearchRecordingError("Адрес не входит в разрешённые хосты сайта")
        recording_key = (user_id, site_key)
        async with self._lock:
            if recording_key in self._active:
                raise SearchRecordingError("Запись уже идёт")
            engine = PlaywrightEngine(
                headless=False,
                timeout_ms=timeout_ms,
                artifact_directory=(artifact_directory / "browser" / "search-recording" / site_key),
                storage_state=storage_state,
            )
            try:
                await engine.__aenter__()
                page = await engine.new_page()
                recording = _ActiveRecording(
                    engine=engine,
                    page=page,
                    allowed_hosts=allowed_hosts,
                    start_url=start_url,
                )

                async def _on_event(_source: object, payload: str) -> None:
                    if len(recording.actions) >= _MAX_RECORDED_ACTIONS:
                        return
                    with suppress(Exception):
                        action = RecordedAction.from_payload(json.loads(payload))
                        if action.kind in ("click", "fill"):
                            recording.actions.append(action)

                await page.expose_binding("__jsaRecordEvent", _on_event)
                await page.add_init_script(path=str(_RECORDER_SCRIPT))
                navigation = await engine.navigate(page, start_url)
                if not navigation.is_successful:
                    raise SearchRecordingError("Не удалось открыть страницу сайта")
            except Exception:
                with suppress(Exception):
                    await engine.__aexit__()
                raise
            self._active[recording_key] = recording

    async def stop(self, *, user_id: str, site_key: str) -> RecordingResult:
        recording_key = (user_id, site_key)
        async with self._lock:
            recording = self._active.pop(recording_key, None)
        if recording is None:
            raise SearchRecordingError("Запись не запущена. Начните запись заново")
        try:
            final_url = recording.page.url
            if not is_allowed_host(urlsplit(final_url).hostname, recording.allowed_hosts):
                raise SearchRecordingError("Сайт перенаправил на неразрешённый хост")
            html = await recording.page.content()
            return RecordingResult(
                start_url=recording.start_url,
                final_url=final_url,
                final_html=html,
                actions=tuple(recording.actions),
            )
        finally:
            with suppress(Exception):
                await recording.engine.__aexit__()

    async def cancel(self, *, user_id: str, site_key: str) -> None:
        recording_key = (user_id, site_key)
        async with self._lock:
            recording = self._active.pop(recording_key, None)
        if recording is not None:
            with suppress(Exception):
                await recording.engine.__aexit__()

    def is_recording(self, *, user_id: str, site_key: str) -> bool:
        return (user_id, site_key) in self._active
