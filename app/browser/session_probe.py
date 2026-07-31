from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from app.browser.engine import PlaywrightEngine

_PROBE_URLS = {
    "headhunter": "https://hh.ru/applicant/resumes",
    "linkedin": "https://www.linkedin.com/feed/",
}


@dataclass(frozen=True, slots=True)
class BrowserSessionProbe:
    is_live: bool | None
    checked_at: datetime
    error: str | None = None


async def probe_browser_session(
    *,
    site_key: str,
    state: dict[str, object],
    headless: bool,
    timeout_ms: int,
    artifact_directory: Path,
) -> BrowserSessionProbe:
    """Check authentication without credentials, form input, or submit actions."""
    checked_at = datetime.now(UTC)
    target_url = _PROBE_URLS.get(site_key)
    if target_url is None:
        return BrowserSessionProbe(None, checked_at, "unsupported site")

    try:
        async with PlaywrightEngine(
            headless=headless,
            timeout_ms=min(timeout_ms, 20_000),
            artifact_directory=artifact_directory / "session-probes" / site_key,
            storage_state=state,
        ) as engine:
            page = await engine.new_page()
            navigation = await engine.navigate(page, target_url)
            if not navigation.is_successful:
                return BrowserSessionProbe(None, checked_at, "site is temporarily unavailable")

            current_url = page.url.casefold()
            hostname = (urlparse(page.url).hostname or "").casefold()
            if site_key == "headhunter":
                trusted_host = hostname == "hh.ru" or hostname.endswith(".hh.ru")
                logged_out = (
                    "/account/login" in current_url
                    or await page.locator(
                        "[data-qa='mainmenu_loginOrRegister'],a[href*='/account/login']"
                    ).count()
                    > 0
                )
            else:
                trusted_host = hostname == "linkedin.com" or hostname.endswith(".linkedin.com")
                logged_out = (
                    any(
                        marker in current_url
                        for marker in ("/login", "/authwall", "/checkpoint/challenge")
                    )
                    or await page.locator(
                        "form.login__form,form[action*='/login-submit'],a[href*='/login']"
                    ).count()
                    > 0
                )
            return BrowserSessionProbe(trusted_host and not logged_out, checked_at)
    except Exception:  # noqa: BLE001 - status endpoint must report an unknown probe, not crash
        return BrowserSessionProbe(None, checked_at, "could not check the session")
