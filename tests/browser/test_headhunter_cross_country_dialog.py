from __future__ import annotations

import asyncio
from pathlib import Path

from adapters.job_boards.headhunter_browser import HeadHunterBrowserAdapter
from app.browser.engine import PlaywrightEngine


def test_headhunter_cross_country_alertdialog_is_confirmed(tmp_path: Path) -> None:
    async def run_dialog() -> None:
        fixture_path = (
            Path(__file__).parents[2] / "fixtures" / "headhunter_cross_country_alertdialog.html"
        )
        async with PlaywrightEngine(artifact_directory=tmp_path) as engine:
            page = await engine.new_page()
            await page.set_content(fixture_path.read_text(encoding="utf-8"))

            action = await HeadHunterBrowserAdapter(engine)._handle_cross_country_dialog(page)

            assert action is not None
            assert action.is_successful
            assert action.target == "continue-cross-country-application"
            assert await page.locator("body").get_attribute("data-confirmed") == "true"

    asyncio.run(run_dialog())
