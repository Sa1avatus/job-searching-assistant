from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from app.browser.custom_site import looks_like_login_page
from app.browser.engine import PlaywrightEngine
from app.domain.search_recipe import is_allowed_host

_PROBE_URLS = {
    "headhunter": "https://hh.ru/applicant/resumes",
    "linkedin": "https://www.linkedin.com/feed/",
}
BUILTIN_SITE_KEYS = frozenset(_PROBE_URLS)


@dataclass(frozen=True, slots=True)
class CustomProbeSite:
    """Where to check liveness for a user-defined site, and what "still on sign-in" means."""

    login_url: str
    allowed_hosts: tuple[str, ...]
    login_path_markers: tuple[str, ...]


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
    custom_site: CustomProbeSite | None = None,
) -> BrowserSessionProbe:
    """Check authentication without credentials, form input, or submit actions."""
    checked_at = datetime.now(UTC)
    target_url = custom_site.login_url if custom_site is not None else _PROBE_URLS.get(site_key)
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
            if custom_site is not None:
                # A site with no session state just shows its own sign-in page for this URL by
                # definition, so a live/expired verdict here is only meaningful once the person
                # has actually signed in at least once (login_path_markers is never empty then).
                trusted_host = is_allowed_host(hostname, custom_site.allowed_hosts)
                logged_out = looks_like_login_page(page.url, custom_site.login_path_markers)
            elif site_key == "headhunter":
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
