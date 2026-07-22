import asyncio
from pathlib import Path

from app.browser.engine import PlaywrightEngine


def test_browser_fills_application_without_submitting(tmp_path: Path) -> None:
    async def run_flow() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "application.html"
        resume_path = tmp_path / "resume.txt"
        resume_path.write_text("Controlled test resume", encoding="utf-8")
        async with PlaywrightEngine(artifact_directory=tmp_path / "artifacts") as engine:
            page = await engine.new_page()
            navigation = await engine.navigate(page, fixture_path.resolve().as_uri())
            actions = await engine.fill_review_form(
                page,
                full_name="Test Candidate",
                email="candidate@example.test",
                resume_path=resume_path,
            )

            assert navigation.is_successful
            assert all(action.is_successful for action in actions)
            assert await page.get_by_label("Full name").input_value() == "Test Candidate"
            assert await page.locator("#confirmation").is_hidden()
            assert actions[-1].screenshot_path is not None

    asyncio.run(run_flow())
