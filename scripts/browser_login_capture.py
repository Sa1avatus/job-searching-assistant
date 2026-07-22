"""Capture a signed-in browser session for hh.ru or LinkedIn by hand.

This is the *only* supported way to give the browser worker a session for
``adapters/job_boards/headhunter_browser.py`` / ``linkedin_browser.py``. It never asks for or
stores a password: it opens a real, visible (non-headless) Chromium window, you log in yourself
-- including any 2FA, SMS code, or CAPTCHA -- and once you confirm you're signed in, the script
saves the resulting cookies/local-storage as an encrypted Playwright storage state tied to your
user id and the site.

Usage:
    python scripts/browser_login_capture.py headhunter --user-id <user-id>
    python scripts/browser_login_capture.py linkedin --user-id <user-id>

Requires APP_BROWSER_STATE_ENCRYPTION_KEY to be set (same key the worker uses), e.g.:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Re-run this whenever hh.ru/LinkedIn signs you out or the worker reports a "reauthenticate"
human-action checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.browser.engine import PlaywrightEngine
from app.browser.session_service import BrowserSessionService
from app.browser.session_store import EncryptedBrowserStateStore
from app.config import get_settings
from app.storage.database import SessionFactory
from app.storage.tables import UserRow

_LOGIN_URLS = {
    "headhunter": "https://hh.ru/account/login",
    "linkedin": "https://www.linkedin.com/login",
}
_ADAPTER_NAMES = {"headhunter": "headhunter", "linkedin": "linkedin"}


async def _capture(site: str, user_id: str) -> int:
    settings = get_settings()
    if settings.browser_state_encryption_key is None:
        print("APP_BROWSER_STATE_ENCRYPTION_KEY must be set before capturing a session.")
        return 1
    with SessionFactory() as session:
        if session.get(UserRow, user_id) is None:
            print(f"No user with id {user_id!r}. Create one first (see app/cli.py import-profile).")
            return 1

    store = EncryptedBrowserStateStore(
        settings.artifact_directory,
        encryption_key=settings.browser_state_encryption_key.get_secret_value(),
        max_state_bytes=settings.max_browser_state_bytes,
    )

    print(f"Opening a visible browser window for {site}. Log in by hand, then come back here.")
    async with PlaywrightEngine(
        headless=False,
        timeout_ms=settings.browser_timeout_ms,
        artifact_directory=settings.artifact_directory / "browser" / "login-capture",
    ) as browser_engine:
        page = await browser_engine.new_page()
        await browser_engine.navigate(page, _LOGIN_URLS[site])
        await asyncio.to_thread(
            input,
            "\nComplete sign-in (including any verification step) in the opened browser window, "
            "then press Enter here to capture the session... ",
        )
        state = await browser_engine.storage_state()
        last_url = page.url

    with SessionFactory() as session:
        BrowserSessionService(session, store).save(
            user_id=user_id,
            site_key=site,
            adapter_name=_ADAPTER_NAMES[site],
            state=state,
            last_url=last_url,
        )
    print(f"Session captured and encrypted for site={site!r}, user_id={user_id!r}.")
    print("The browser worker will use it automatically for future apply tasks.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", choices=sorted(_LOGIN_URLS))
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    return asyncio.run(_capture(args.site, args.user_id))


if __name__ == "__main__":
    sys.exit(main())
