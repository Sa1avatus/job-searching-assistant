import asyncio
import json
from pathlib import Path

from app.browser.engine import PlaywrightEngine
from app.browser.evidence import capture_browser_failure


def test_browser_failure_captures_page_evidence_without_secret_values(tmp_path: Path) -> None:
    async def run_capture() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "application.html"
        async with PlaywrightEngine(artifact_directory=tmp_path) as engine:
            page = await engine.new_page()
            await engine.navigate(page, fixture_path.resolve().as_uri())
            evidence = await capture_browser_failure(
                page,
                artifact_directory=tmp_path,
                action_name="fill",
                target="Missing field",
                error=LookupError("selector missing"),
            )

        metadata = json.loads(evidence.metadata_path.read_text(encoding="utf-8"))
        assert evidence.screenshot_path.exists()
        assert evidence.html_path.exists()
        assert metadata["error_type"] == "LookupError"
        assert "selector missing" not in evidence.metadata_path.read_text(encoding="utf-8")

    asyncio.run(run_capture())
