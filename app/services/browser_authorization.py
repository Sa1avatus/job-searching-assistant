from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import structlog
from playwright.async_api import Page

from app.browser.engine import PlaywrightEngine

logger = structlog.get_logger(__name__)

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


@dataclass(frozen=True, slots=True)
class BrowserAuthorizationSite:
    site_key: str
    login_url: str
    allowed_hosts: tuple[str, ...]
    login_path_markers: tuple[str, ...]
    allow_subdomains: bool = False


KNOWN_AUTHORIZATION_SITES: dict[str, BrowserAuthorizationSite] = {
    site_key: BrowserAuthorizationSite(
        site_key=site_key,
        login_url=LOGIN_URLS[site_key],
        allowed_hosts=ALLOWED_HOST_SUFFIXES[site_key],
        login_path_markers=LOGIN_PATH_MARKERS[site_key],
        allow_subdomains=True,
    )
    for site_key in LOGIN_URLS
}


@dataclass(slots=True)
class ActiveBrowserAuthorization:
    engine: PlaywrightEngine
    page: Page
    site: BrowserAuthorizationSite


class BrowserAuthorizationManager:
    """Own visible, short-lived login browsers started explicitly from the local dashboard."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], ActiveBrowserAuthorization] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        user_id: str,
        site_key: str,
        timeout_ms: int,
        artifact_directory: Path,
        site: BrowserAuthorizationSite | None = None,
    ) -> None:
        resolved_site = site or KNOWN_AUTHORIZATION_SITES.get(site_key)
        if resolved_site is None or resolved_site.site_key != site_key:
            raise BrowserAuthorizationError("Site authorization configuration is missing")
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
                navigation = await engine.navigate(page, resolved_site.login_url)
                if not navigation.is_successful:
                    raise BrowserAuthorizationError("Не удалось открыть страницу входа")
            except Exception:
                with suppress(Exception):
                    await engine.__aexit__()
                raise
            self._active[authorization_key] = ActiveBrowserAuthorization(
                engine,
                page,
                resolved_site,
            )

    async def confirm(self, *, user_id: str, site_key: str) -> tuple[dict[str, object], str]:
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
            hostname == allowed_host
            or (authorization.site.allow_subdomains and hostname.endswith(f".{allowed_host}"))
            for allowed_host in authorization.site.allowed_hosts
        )
        is_login_page = any(
            marker.casefold() in path for marker in authorization.site.login_path_markers
        )
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

    async def cancel(self, *, user_id: str, site_key: str) -> None:
        authorization_key = (user_id, site_key)
        async with self._lock:
            authorization = self._active.pop(authorization_key, None)
        if authorization is not None:
            with suppress(Exception):
                await authorization.engine.__aexit__()

    def is_waiting(self, *, user_id: str, site_key: str) -> bool:
        return (user_id, site_key) in self._active
