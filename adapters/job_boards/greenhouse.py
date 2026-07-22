from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from adapters.job_boards.contracts import ReviewPreparation
from app.browser.engine import BrowserActionResult, PlaywrightEngine
from app.browser.form_discovery import discover_form_fields
from app.domain.forms import FormField


class GreenhouseAdapter:
    name = "greenhouse"
    _hosts = frozenset({"boards.greenhouse.io", "job-boards.greenhouse.io"})

    def __init__(self, browser_engine: PlaywrightEngine) -> None:
        self._browser_engine = browser_engine

    def supports_url(self, url: str) -> bool:
        hostname = (urlparse(url).hostname or "").casefold()
        return hostname in self._hosts

    async def discover_form_fields(self, url: str) -> tuple[FormField, ...]:
        if not self.supports_url(url):
            raise ValueError("URL is not a supported Greenhouse host")
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, url)
        if not navigation.is_successful:
            raise RuntimeError(f"Greenhouse navigation failed: {navigation.error_category}")
        if not self.supports_url(page.url):
            raise RuntimeError("Greenhouse navigation left the trusted job-board hosts")
        return await discover_form_fields(page)

    async def prepare_review(
        self,
        url: str,
        answers: Mapping[str, str | bool | None],
    ) -> ReviewPreparation:
        """Fill validated answers and stop before the irreversible submit action."""
        if not self.supports_url(url):
            raise ValueError("URL is not a supported Greenhouse host")
        page = await self._browser_engine.new_page()
        navigation = await self._browser_engine.navigate(page, url)
        if not navigation.is_successful:
            raise RuntimeError(f"Greenhouse navigation failed: {navigation.error_category}")
        if not self.supports_url(page.url):
            raise RuntimeError("Greenhouse navigation left the trusted job-board hosts")
        fields = await discover_form_fields(page)
        known_field_ids = {field.field_id for field in fields}
        unknown_field_ids = sorted(set(answers) - known_field_ids)
        if unknown_field_ids:
            raise ValueError(f"Answers contain unknown field IDs: {', '.join(unknown_field_ids)}")

        actions: list[BrowserActionResult] = []
        for field in fields:
            answer = answers.get(field.field_id, field.current_value)
            if field.field_id in answers or field.is_required:
                actions.append(
                    await self._browser_engine.fill_discovered_field(
                        page,
                        field,
                        answer,
                        adapter_name=self.name,
                    )
                )
        if fields and all(action.is_successful for action in actions):
            actions.append(
                await self._browser_engine.capture_review_checkpoint(
                    page, target="greenhouse-review-before-submit"
                )
            )
        return ReviewPreparation(fields=fields, actions=tuple(actions))


def normalize_greenhouse_resume_path(path: Path) -> str:
    """Return an absolute upload value so validation and Playwright resolve the same file."""
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Resume path must reference a file")
    return str(resolved)
