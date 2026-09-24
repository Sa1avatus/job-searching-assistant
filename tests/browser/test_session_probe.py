"""Liveness probing for a custom site whose sign-in and post-login account area share a URL
path (e.g. Michael Page's "/mypage/") - the marker alone can't tell them apart, so the probe
must also look at whether a password field is actually on the page.
"""

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import Page, Route

from app.browser import session_probe
from app.browser.engine import PlaywrightEngine
from app.browser.session_probe import CustomProbeSite, probe_browser_session

SITE = CustomProbeSite(
    login_url="https://www.example.com/mypage/",
    allowed_hosts=("www.example.com",),
    login_path_markers=("/mypage/",),
)


def _routed_engine(tmp_path: Path, serve) -> PlaywrightEngine:
    engine = PlaywrightEngine(artifact_directory=tmp_path)
    original = engine.new_page

    async def routed() -> Page:
        page = await original()
        await page.route("**/*", serve)
        return page

    engine.new_page = routed  # type: ignore[method-assign]
    return engine


async def _serve_login_form(route: Route) -> None:
    await route.fulfill(
        status=200,
        content_type="text/html",
        body="<html><body><form><input type='password'></form></body></html>",
    )


async def _serve_dashboard(route: Route) -> None:
    await route.fulfill(
        status=200,
        content_type="text/html",
        body="<html><body><h1>Welcome SALAVAT!</h1><p>Profile Completeness 90%</p></body></html>",
    )


def test_probe_reports_expired_when_the_shared_path_still_shows_a_password_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        session_probe,
        "PlaywrightEngine",
        lambda **kwargs: _routed_engine(tmp_path, _serve_login_form),
    )

    result = asyncio.run(
        probe_browser_session(
            site_key="michaelpage",
            state={"cookies": []},
            headless=True,
            timeout_ms=5_000,
            artifact_directory=tmp_path,
            custom_site=SITE,
        )
    )

    assert result.is_live is False


def test_probe_reports_live_when_the_shared_path_shows_the_account_area_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        session_probe,
        "PlaywrightEngine",
        lambda **kwargs: _routed_engine(tmp_path, _serve_dashboard),
    )

    result = asyncio.run(
        probe_browser_session(
            site_key="michaelpage",
            state={"cookies": []},
            headless=True,
            timeout_ms=5_000,
            artifact_directory=tmp_path,
            custom_site=SITE,
        )
    )

    assert result.is_live is True
