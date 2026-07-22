import asyncio
from pathlib import Path

from app.browser.engine import PlaywrightEngine
from app.browser.selector_library import SelectorKind, SelectorLibrary
from app.domain.forms import FormField, FormFieldType


def test_selector_recovery_is_bounded_promoted_and_reused(tmp_path: Path) -> None:
    async def run_recovery() -> None:
        fixture_path = Path(__file__).parents[1] / "fixtures" / "application_redesigned.html"
        selector_library = SelectorLibrary(tmp_path)
        field = FormField(
            field_id="email",
            label="Email",
            field_type=FormFieldType.TEXT,
            is_required=True,
            semantic_category="email",
            confidence=0.95,
            source_locator="label:Email",
        )
        async with PlaywrightEngine(
            artifact_directory=tmp_path,
            selector_library=selector_library,
            timeout_ms=500,
            max_selector_attempts=3,
        ) as engine:
            page = await engine.new_page()
            assert (await engine.navigate(page, fixture_path.resolve().as_uri())).is_successful
            recovered = await engine.fill_discovered_field(
                page,
                field,
                "candidate@example.test",
                adapter_name="controlled",
            )
            assert recovered.is_successful
            assert recovered.selector_attempts == (
                "label:Email",
                "placeholder:Email",
                "id:email",
            )
            assert await page.locator("#email").input_value() == "candidate@example.test"

            await page.locator("#email").fill("")
            reused = await engine.fill_discovered_field(
                page,
                field,
                "restored@example.test",
                adapter_name="controlled",
            )
            assert reused.is_successful
            assert reused.selector_attempts == ("id:email",)

        versions = selector_library.versions("controlled", "email")
        assert len(versions) == 1
        assert versions[0].kind is SelectorKind.ID
        assert versions[0].is_active

    asyncio.run(run_recovery())
