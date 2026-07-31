from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from playwright.async_api import Page

from app.browser.engine import PlaywrightEngine

BrowserSiteKey = Literal["headhunter", "linkedin"]
LOGIN_URLS: dict[BrowserSiteKey, str] = {
    "headhunter": "https://hh.ru/account/login",
    "linkedin": "https://www.linkedin.com/login",
}
LOGIN_PATH_MARKERS: dict[BrowserSiteKey, tuple[str, ...]] = {
    "headhunter": ("/account/login", "/account/signup", "/account/captcha"),
    "linkedin": ("/login", "/checkpoint", "/authwall"),
}
ALLOWED_HOST_SUFFIXES: dict[BrowserSiteKey, tuple[str, ...]] = {
    "headhunter": ("hh.ru",),
    "linkedin": ("linkedin.com",),
}


class BrowserAuthorizationError(RuntimeError):
    pass


@dataclass(slots=True)
class ActiveBrowserAuthorization:
    engine: PlaywrightEngine
    page: Page


class BrowserAuthorizationManager:
    """Own visible, short-lived login browsers started explicitly from the local dashboard."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, BrowserSiteKey], ActiveBrowserAuthorization] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        user_id: str,
        site_key: BrowserSiteKey,
        timeout_ms: int,
        artifact_directory: Path,
    ) -> None:
        authorization_key = (user_id, site_key)
        async with self._lock:
            if authorization_key in self._active:
                raise BrowserAuthorizationError("Окно авторизации уже открыто")
            engine = PlaywrightEngine(
                headless=False,
                timeout_ms=timeout_ms,
                artifact_directory=artifact_directory / "browser" / "login-capture" / site_key,
            )
            try:
                await engine.__aenter__()
                page = await engine.new_page()
                navigation = await engine.navigate(page, LOGIN_URLS[site_key])
                if not navigation.is_successful:
                    raise BrowserAuthorizationError("Не удалось открыть страницу входа")
            except Exception:
                with suppress(Exception):
                    await engine.__aexit__()
                raise
            self._active[authorization_key] = ActiveBrowserAuthorization(engine, page)

    async def confirm(
        self, *, user_id: str, site_key: BrowserSiteKey
    ) -> tuple[dict[str, object], str]:
        authorization_key = (user_id, site_key)
        async with self._lock:
            authorization = self._active.get(authorization_key)
        if authorization is None:
            raise BrowserAuthorizationError("Окно авторизации не открыто. Нажмите кнопку входа")
        current_url = authorization.page.url
        parsed_url = urlparse(current_url)
        hostname = (parsed_url.hostname or "").casefold()
        path = parsed_url.path.casefold()
        is_expected_host = any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in ALLOWED_HOST_SUFFIXES[site_key]
        )
        is_login_page = any(marker in path for marker in LOGIN_PATH_MARKERS[site_key])
        if not is_expected_host or is_login_page:
            raise BrowserAuthorizationError(
                "Вход ещё не завершён. Закончите авторизацию в открытом окне и повторите"
            )
        async with self._lock:
            self._active.pop(authorization_key, None)
        try:
            state = await authorization.engine.storage_state()
            return state, current_url
        except Exception as error:
            raise BrowserAuthorizationError(
                "Окно авторизации закрыто до сохранения сессии. Начните вход заново"
            ) from error
        finally:
            with suppress(Exception):
                await authorization.engine.__aexit__()

    async def cancel(self, *, user_id: str, site_key: BrowserSiteKey) -> None:
        authorization_key = (user_id, site_key)
        async with self._lock:
            authorization = self._active.pop(authorization_key, None)
        if authorization is not None:
            with suppress(Exception):
                await authorization.engine.__aexit__()

    def is_waiting(self, *, user_id: str, site_key: BrowserSiteKey) -> bool:
        return (user_id, site_key) in self._active
