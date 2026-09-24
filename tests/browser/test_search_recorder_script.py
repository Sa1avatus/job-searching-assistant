"""Proves the fixed recorder script (app/browser/assets/search_recorder.js) correctly reports
clicks and completed field edits, and never reports a password field - without going through
the recording manager (headless, safe for CI; no visible browser needed for this proof)."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import Route

from app.browser.engine import PlaywrightEngine

SCRIPT_PATH = Path(__file__).parents[2] / "app" / "browser" / "assets" / "search_recorder.js"

FORM_HTML = """
<html><body>
<input type="text" id="query-input" placeholder="Search jobs">
<input type="password" id="password-input">
<button type="button" id="search-button">Search</button>
</body></html>
"""


async def _serve(route: Route) -> None:
    await route.fulfill(status=200, content_type="text/html", body=FORM_HTML)


def test_recorder_reports_a_field_edit_and_a_click_but_never_a_password(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []

    async def run() -> None:
        async with PlaywrightEngine(artifact_directory=tmp_path) as engine:
            page = await engine.new_page()
            await page.route("**/*", _serve)

            async def _on_event(_source: object, payload: str) -> None:
                events.append(json.loads(payload))

            await page.expose_binding("__jsaRecordEvent", _on_event)
            await page.add_init_script(path=str(SCRIPT_PATH))
            await engine.navigate(page, "https://jobs.example.com/")

            await page.locator("#query-input").fill("python developer")
            await page.locator("#query-input").press("Tab")
            await page.locator("#password-input").fill("hunter2")
            await page.locator("#password-input").press("Tab")
            await page.click("#search-button")

    asyncio.run(run())

    kinds = [(event["kind"], event.get("id")) for event in events]
    assert ("fill", "query-input") in kinds
    assert ("click", "search-button") in kinds
    assert not any(event.get("id") == "password-input" for event in events)

    fill_event = next(event for event in events if event.get("id") == "query-input")
    assert fill_event["value"] == "python developer"
    assert fill_event["valueLength"] == 16
    assert fill_event["placeholder"] == "Search jobs"

    click_event = next(event for event in events if event.get("id") == "search-button")
    assert click_event["text"] == "Search"
    assert "value" not in click_event
